import logging
import secrets
import hashlib
import hmac
import json
from abc import ABC
from collections import defaultdict
from itertools import chain
from datetime import datetime, timedelta
from enum import IntEnum, Enum, auto

import celery
from babel import Locale

from flask import Flask, abort, request, jsonify, render_template
from flask_babel import Babel, get_locale, format_timedelta
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.declarative import declared_attr
from sqlalchemy.orm import validates
from sqlalchemy import UniqueConstraint, select, update
from flask_sqlalchemy import SQLAlchemy

import boggle_utils
import config

from boggle_celery import app as celery_app

logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder='static', static_url_path='/static')


app.config.from_object(config)
app.config['SECRET_KEY'] = server_key = secrets.token_bytes(32)
db = SQLAlchemy(app)
babel = Babel(app, default_domain='boggle')

DATE_FORMAT_STR = '%Y-%m-%d %H:%M:%S'
MAX_NAME_LENGTH = 250


dice_configs = boggle_utils.DiceConfigServiceProvider(
    app.config['DICE_CONFIG_DIR']
)


def init_db():
    """
    Set up the database schema and/or truncate all sessions.
    """
    # create tables as necessary
    #  we don't use create_all because the effective score wrapper
    #  should never be created by SQLAlchemy
    bind = db.session.bind
    for Model in (BoggleSession, Player, Submission, Word):
        Model.__table__.create(bind, checkfirst=True)

    with db.engine.connect() as con:
        with con.begin():
            # truncate all sessions on every restart
            con.execute('TRUNCATE boggle_session RESTART IDENTITY CASCADE;')

            # if necessary, set up the views for computing effective scores
            #  in the DB
            if app.config['EFFECTIVE_SCORE_SQL']:
                logger.debug('Loading effective score views...')
                with open('effective_score.sql', 'r') as sql_file:
                    sql = sql_file.read()
                con.execute(sql)
            else:
                logger.debug('Skipping effective scoring views...')


class ScoringScheme(Enum):
    BASIC = auto()
    SQL = auto()
    # alternative scoring scheme that awards points more easily
    #  (only supported in the SQL scoring view, because meh)
    SQL_MILD = auto()

# adding before_first_request to init_db would cause this to be run
#  for every worker, which isn't what we want.
# In prod, a a CLI command seems to involve the least amount of hassle
app.cli.command('initdb')(init_db)

if __name__ == '__main__':
    init_db()
    app.run()


def json_err_handler(error_code):
    return lambda e: (jsonify(error=str(e)), error_code)


for err in (400, 403, 404, 409, 410, 501):
    app.register_error_handler(err, json_err_handler(err))


class BoggleSession(db.Model):
    __tablename__ = 'boggle_session'

    id = db.Column(db.Integer, primary_key=True)
    created = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    dice_config = db.Column(db.String(250), nullable=False)
    dictionary = db.Column(db.String(250), nullable=True)
    # TODO actually allow the user to specify this value
    round_minutes = db.Column(db.Integer, nullable=False,
                              default=app.config['ROUND_DURATION_MINUTES'])
    use_mild_scoring = db.Column(db.Boolean, nullable=False, default=False)

    # volatile data
    round_no = db.Column(db.Integer, nullable=False, default=0)
    round_start = db.Column(db.DateTime, nullable=True)
    round_scored = db.Column(db.Boolean, nullable=True)

    @classmethod
    def for_update(cls, session_id, *, allow_nonexistent=False):
        q = cls.query.filter(cls.id == session_id).with_for_update()
        if allow_nonexistent:
            return q.one_or_none()
        else:
            return q.one()

    # included for easy testing access
    @classmethod
    def _submissions_pending(cls, session_id, round_no):
        submissions = select([Submission.id, Submission.player_id])\
            .where(Submission.round_no == round_no).alias()
        q = Player.query.outerjoin(
                submissions, submissions.c.player_id == Player.id
            ).filter(Player.session_id == session_id)\
            .filter(submissions.c.id.is_(None))
        return q.exists()

    def submissions_pending(self):
        """
        Build an exists() query to check if there are players in the session
        that still have to submit a score.
        """
        return self._submissions_pending(self.id, self.round_no)

    def __repr__(self):
        fmt_ts = self.created.now().strftime(DATE_FORMAT_STR)
        return '<Session %s>' % fmt_ts


class Player(db.Model): 
    __tablename__ = 'player'

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(
        db.Integer, db.ForeignKey('boggle_session.id', ondelete="cascade"),
        nullable=False,
    )
    session = db.relationship(
        BoggleSession, backref=db.backref('players')
    )
    name = db.Column(db.String(MAX_NAME_LENGTH), nullable=False)

    def __repr__(self):
        return '<Player %r (%r)>' % (self.name, self.id)


class Submission(db.Model):
    __tablename__ = 'submission'
    __table_args__ = (
        UniqueConstraint('player_id', 'round_no'),
    )

    id = db.Column(db.Integer, primary_key=True)
    player_id = db.Column(
        db.Integer, db.ForeignKey('player.id', ondelete="cascade"),
        nullable=False
    )
    player = db.relationship(
        Player, backref=db.backref('submissions')
    )
    round_no = db.Column(db.Integer, nullable=False)


class AbstractWord(db.Model):
    __abstract__ = True

    id = db.Column(db.Integer, primary_key=True)

    @declared_attr
    def submission_id(cls):
        return db.Column(
            db.Integer, db.ForeignKey('submission.id', ondelete="cascade"),
            nullable=False
        )

    word = db.Column(db.String(20), nullable=False)

    score = db.Column(db.Integer, nullable=True)
    duplicate = db.Column(db.Boolean, nullable=True)
    dictionary_valid = db.Column(db.Boolean, nullable=True)
    # we store this as text, since it's only needed for UI feedback
    path_array = db.Column(db.Text, nullable=True)

    def score_json(self):
        return {
            'score': self.score if self.dictionary_valid else 0,
            'word': self.word,
            'in_grid': self.path_array is not None,
            'duplicate': self.duplicate,
            'dictionary_valid': self.dictionary_valid,
            # this is a bit silly, since we'll just reencode
            #  it right away, but it's the most straightforward
            #  way to go about it
            'path': (
                json.loads(self.path_array)
                if self.path_array is not None else None
            )
        }

    # noinspection PyUnusedLocal
    @validates('word')
    def to_uppercase(self, key, value):
        return value.upper()


class Word(AbstractWord):
    __tablename__ = 'word'
    __table_args__ = (
        UniqueConstraint('submission_id', 'word'),
    )

    submission = db.relationship(
        Submission, backref=db.backref('words')
    )


# wrapper around the effective_scores view
class WordWithEffectiveScore(AbstractWord):
    __tablename__ = 'effective_scores'

    # no-backref version
    submission = db.relationship(Submission)

    longest_bonus = db.Column(db.Boolean, nullable=False)
    score_mild = db.Column(db.Integer, nullable=False)

    def score_json(self):
        result = super().score_json()
        result['longest_bonus'] = self.longest_bonus
        return result


class WordWithEffectiveMildScore(WordWithEffectiveScore):
    @property
    def score(self):
        return self.score_mild


# wrapper around the statistics view
class Statistics(db.Model):
    __tablename__ = 'statistics'

    id = db.Column(db.Integer, primary_key=True)
    player_id = db.Column(
        db.Integer, db.ForeignKey('player.id'),
        nullable=False
    )
    player = db.relationship(Player, backref=db.backref('statistics'))
    round_no = db.Column(db.Integer, nullable=True)
    total_score = db.Column(db.Integer, nullable=False)


def gen_salted_token(salt, *args):
    hmac_key = hashlib.sha1(salt + server_key).digest()
    token_data = ''.join(str(d) for d in args)
    salted_hmac = hmac.new(
        hmac_key, msg=token_data.encode('ascii'),
        digestmod=hashlib.sha1
    )
    token_hash = salted_hmac.hexdigest()[::2]
    assert len(token_hash) == 20
    return token_hash


def gen_session_mgmt_token(session_id, pepper):
    return gen_salted_token(b'sessman', session_id, pepper)


def gen_session_inv_token(session_id, pepper):
    return gen_salted_token(b'session', session_id, pepper)


def gen_player_token(session_id, player_id, pepper):
    return gen_salted_token(b'player', session_id, pepper, player_id)


supported_locales = [
    Locale.parse(locale) for locale in app.config['BABEL_SUPPORTED_LOCALES']
]

app.add_template_filter(format_timedelta)


@app.route('/', methods=['GET'])
def index():
    return render_template(
        'boggle.html', api_base_url=app.config['API_BASE_URL'],
        default_countdown=app.config['DEFAULT_COUNTDOWN_SECONDS'],
        active_locale=get_locale(),
        available_locales=supported_locales,
        # help template parameters
        base_score_values=app.config['BASE_SCORE_VALUES'],
        default_round_duration=timedelta(
            minutes=app.config['ROUND_DURATION_MINUTES']
        )
    )


@babel.localeselector
def select_locale():
    try:
        return request.args['lang']
    except KeyError:
        return request.accept_languages.best_match(
            app.config['BABEL_SUPPORTED_LOCALES']
        )


@app.route('/options', methods=['GET'])
def list_options():
    return {
        'dictionaries': trigger_scoring.dicts_available,
        'dice_configs': list(dice_configs),
        'statistics': app.config['EFFECTIVE_SCORE_SQL']
    }


@app.route('/session', methods=['POST'])
def spawn_session():
    session_settings = request.get_json()
    dicts_available = trigger_scoring.dicts_available
    no_dic_requested = False
    selected_dictionary = None
    selected_dice_config = app.config['DEFAULT_DICE_CONFIG']
    use_mild_scoring = False
    if session_settings is not None:
        # set chosen dictionary
        try:
            selected_dictionary = session_settings['dictionary']
            # if None is passed in, we disable the dictionary
            if selected_dictionary is None:
                no_dic_requested = True
            elif selected_dictionary not in dicts_available:
                abort(
                    404,
                    f"The dictionary '{selected_dictionary}' is not available"
                )
        except KeyError:
            pass

        # set chosen dice config
        selected_dice_config = session_settings.get(
            'dice_config', selected_dice_config
        )
        if selected_dice_config not in dice_configs:
            abort(
                404,
                f"The dice configuration '{selected_dice_config}' is "
                "not available."
            )

        use_mild_scoring = session_settings.get('mild_scoring', False)

    if selected_dictionary is None and not no_dic_requested:
        try:
            selected_dictionary, = dicts_available
        except ValueError:
            # can't select a default
            # TODO allow env-specified default
            pass

    new_session = BoggleSession(
        dictionary=selected_dictionary, dice_config=selected_dice_config,
        use_mild_scoring=use_mild_scoring
    )
    db.session.add(new_session)
    db.session.commit()
    pepper = secrets.token_bytes(8).hex()
    sess_id = new_session.id

    return {
        'session_id': sess_id,
        'pepper': pepper,
        'session_mgmt_token': gen_session_mgmt_token(sess_id, pepper),
        'session_token': gen_session_inv_token(sess_id, pepper)
    }, 201


session_url_base = '/session/<int:session_id>/<pepper>'
mgmt_url = session_url_base + '/manage/<mgmt_token>'
play_url = session_url_base + '/play/<int:player_id>/<player_token>'


def check_mgmt_token(session_id, pepper, mgmt_token):
    true_token = gen_session_mgmt_token(session_id, pepper)
    if mgmt_token != true_token:
        abort(403, description="Bad session management token")


def check_inv_token(session_id, pepper, inv_token):
    true_token = gen_session_inv_token(session_id, pepper)
    if inv_token != true_token:
        abort(403, description="Bad session token")


@app.route(mgmt_url, methods=['GET', 'POST', 'DELETE'])
def manage_session(session_id, pepper, mgmt_token):
    check_mgmt_token(session_id, pepper, mgmt_token)

    if request.method == 'GET':
        return session_state(session_id, pepper)

    if request.method == 'DELETE':
        BoggleSession.query.filter(BoggleSession.id == session_id).delete()
        db.session.commit()
        return jsonify({}), 204

    if request.method == 'POST':
        # prepare a new round
        sess: BoggleSession = BoggleSession.for_update(
            session_id, allow_nonexistent=True
        )
        if sess is None:
            abort(410, "Session has ended")
        player_q = Player.query.filter(Player.session_id == session_id)
        if db.session.query(~player_q.exists()).scalar():
            return abort(409, "Cannot advance round without players")
        # TODO delete scores if we're skipping ahead

        # if a scoring computation is running right now, this isn't allowed
        #  note that sess.round_scored being None is not an issue
        if sess.round_scored is False:
            return abort(409, "Round cannot be advanced mid-scoring")
        sess.round_scored = None
        json_data = request.get_json()
        until_start = app.config['DEFAULT_COUNTDOWN_SECONDS']
        if json_data is not None:
            until_start = json_data.get('until_start', until_start)
        sess.round_start = datetime.utcnow() + timedelta(seconds=until_start)
        sess.round_no += 1
        db.session.commit()
        return {
            'round_no': sess.round_no,
            'round_start': sess.round_start.strftime(DATE_FORMAT_STR)
        }


@app.route(session_url_base + '/join/<inv_token>', methods=['POST'])
def session_join(session_id, pepper, inv_token):
    check_inv_token(session_id, pepper, inv_token)

    sess = BoggleSession.query.get(session_id)
    submission_json = request.get_json()
    if submission_json is None:
        return abort(400, description="Malformed submission data")
    try:
        name = submission_json['name'][:MAX_NAME_LENGTH]
    except KeyError:
        return abort(400, description="'Name' is required")
    p = Player(name=name)
    sess.players.append(p)
    db.session.commit()
    return {
        'player_id': p.id,
        'player_token': gen_player_token(session_id, p.id, pepper),
        'name': name
    }, 201


class Status(IntEnum):
    # waiting for start announcement
    INITIAL = 0
    # waiting for game to start
    PRE_START = 1
    # game is ongoing
    PLAYING = 2
    # waiting for scores to be submitted
    SCORING = 3
    # scores submitted
    SCORED = 4


def check_player_token(session_id, pepper, player_id, player_token):
    true_token = gen_player_token(session_id, player_id, pepper)
    if player_token != true_token:
        abort(403, description="Bad player token")


def session_state(session_id, pepper):
    sess: BoggleSession = BoggleSession.query\
        .filter(BoggleSession.id == session_id).one_or_none()
    if sess is None:
        abort(410, description="Session has ended")
    players = Player.query.with_parent(sess).all()
    response = {
        'created': sess.created,
        'players': [{'player_id': p.id, 'name': p.name} for p in players]
    }

    round_start = sess.round_start
    if round_start is None:
        response['status'] = Status.INITIAL
        return response
    round_no = sess.round_no
    if app.config['TESTING']:
        round_seed = app.config['TESTING_SEED']
    else:
        round_seed = str(round_no) + pepper + server_key.hex()
    duration = timedelta(minutes=sess.round_minutes)
    round_end = round_start + duration
    now = datetime.utcnow()
    response['round_start'] = round_start.strftime(DATE_FORMAT_STR)
    response['round_end'] = round_end.strftime(DATE_FORMAT_STR)
    response['round_no'] = round_no

    if now < round_start:
        response['status'] = Status.PRE_START
        return response

    dice = dice_configs[sess.dice_config]
    (rows, cols), board = boggle_utils.roll(round_seed, dice_config=dice)
    response['board'] = {'cols': cols, 'rows': rows, 'dice': board}

    all_submitted = db.session.query(~sess.submissions_pending()).scalar()
    if now < round_end and not all_submitted:
        response['status'] = Status.PLAYING
        return response

    grace_period = timedelta(seconds=app.config['GRACE_PERIOD_SECONDS'])

    if (all_submitted or now > round_end + grace_period) \
            and sess.round_scored is None:

        # mainly for testing purposes
        if app.config['DISABLE_ASYNC_SCORING']:
            trigger_scoring(session_id, round_no, round_seed, sess.dice_config)
            # refresh session object
            sess = BoggleSession.query \
                .filter(BoggleSession.id == session_id).one_or_none()
        else:
            # asynchronously queue scoring
            trigger_scoring.delay(
                session_id, round_no, round_seed, sess.dice_config
            )

    if sess.round_scored:
        # if scores have been computed by now, return them
        scores = emit_scores(sess)
        response['status'] = Status.SCORED
        response['scores'] = list(scores)
        return response

    # either the scoring computation is running, or
    #  not everyone has submitted yet, and the grace period is still in effect
    response['status'] = Status.SCORING
    return response


@app.route(play_url, methods=['GET', 'PUT', 'DELETE'])
def play(session_id, pepper, player_id, player_token):
    check_player_token(session_id, pepper, player_id, player_token)

    # the existence check happens later, so in principle players who
    #  left the session can still watch
    if request.method == 'GET':
        return session_state(session_id, pepper)

    sess = BoggleSession.for_update(session_id, allow_nonexistent=True)
    if sess is None:
        return abort(410, description="Session has ended")

    # TODO how does leaving affect the effective_score view? We should
    #  probably tweak the cascading settings accordingly
    if request.method == 'DELETE':
        # TODO can we somehow queue this in an elegant way?
        if sess.round_scored is False:
            abort(409, description="Cannot leave mid-scoring")
        Player.query.filter(Player.id == session_id).delete()
        db.session.commit()
        return jsonify({}), 204

    now = datetime.utcnow()
    current_player = Player.query.filter(Player.id == player_id).one_or_none()
    if current_player is None:
        abort(410, description="You cannot submit after leaving")

    round_start = sess.round_start
    if round_start is None:
        return abort(409, description="Round not started")

    round_no = sess.round_no
    deadline = round_start + timedelta(
        minutes=sess.round_minutes, seconds=app.config['GRACE_PERIOD_SECONDS']
    )
    if sess.round_scored is not None or now > deadline:
        return abort(409, description="Round already ended")

    submission_json = request.get_json()
    if submission_json is None:
        return abort(400, description="Malformed submission data")
    try:
        words = submission_json['words']
        round_no_supplied = int(submission_json['round_no'])
    except KeyError:
        return abort(400, description="Submission not properly structured")
    if round_no != round_no_supplied:
        errmsg = "Wrong round %d, currently round %d" % (
            round_no_supplied, round_no
        )
        return abort(409, description=errmsg)
    submission_obj = Submission(round_no=round_no)
    current_player.submissions.append(submission_obj)
    for word in set(boggle_utils.BoggleWord(w) for w in words if w):
        # TODO can this be done in one append()?
        submission_obj.words.append(Word(word=str(word)))
    try:
        db.session.commit()
    except IntegrityError:
        # due to row locking, a session kill
        #  cannot have occurred in the meantime
        db.session.rollback()
        return abort(409, description="You can only submit once")

    return jsonify({}), 201


@app.route(session_url_base + '/stats/<inv_token>')
def stats(session_id, pepper, inv_token):
    check_inv_token(session_id, pepper, inv_token)

    if not app.config['EFFECTIVE_SCORE_SQL']:
        return abort(501, "Statistics aggregation is not available")

    def query():
        # only fetch the player-global totals
        stats_query = Player.query.join(Statistics).filter(
            Player.session_id == session_id, Statistics.round_no.is_(None)
        ).add_columns(Statistics.total_score)
        for player, total_score in stats_query.all():
            yield {
                'player': {'player_id': player.id, 'name': player.name},
                'total_score': total_score
            }

    return {
        'total_scores': list(query())
    }


@app.route(mgmt_url + '/approve_word', methods=['PATCH'])
def approve_word(session_id, pepper, mgmt_token):
    check_mgmt_token(session_id, pepper, mgmt_token)

    sess: BoggleSession = BoggleSession.for_update(
        session_id, allow_nonexistent=True
    )
    if sess is None:
        abort(410, description="Session already ended")

    if not sess.round_scored:
        abort(409,
              description="This functionality is only available after scoring")

    json_data = request.get_json()
    if json_data is None:
        abort(400, description="No word data supplied")

    try:
        words = set(w.upper() for w in json_data['words'])
    except (KeyError, AttributeError):
        return abort(400, description="No word data supplied")

    update_q = update(Word).values(dictionary_valid=True)\
        .where(Word.word.in_(words))\
        .where(Word.submission_id == Submission.id)\
        .where(Submission.round_no == sess.round_no)\
        .where(Submission.player_id == Player.id)\
        .where(Player.session_id == session_id)

    db.session.execute(update_q)
    db.session.commit()

    scores = emit_scores(sess)
    return {
        'scores': list(scores)
    }


def emit_scores(sess: BoggleSession):
    scored_sql = app.config['EFFECTIVE_SCORE_SQL']
    if scored_sql:
        scheme = ScoringScheme.SQL_MILD if sess.use_mild_scoring \
            else ScoringScheme.SQL
    else:
        scheme = ScoringScheme.BASIC
    by_player = retrieve_submitted_words(
        sess.id, round_no=sess.round_no, scoring_scheme=scheme
    )
    if scored_sql:
        def emitter():
            for (pl_id, pl_name), words in by_player.items():
                yield {
                    'player': {'player_id': pl_id, 'name': pl_name},
                    'words': sorted(
                        (w.score_json() for w in words), key=lambda w: w['word']
                    )
                }
        return emitter()
    else:
        return format_scores(by_player, sess.use_mild_scoring)


def format_scores(by_player, use_mild_scoring):
    longest = 0
    longest_unique = True

    # figure out if there are multiple players with a word of maximal length
    for words in by_player.values():
        max_len = max(
            (len(w.word) for w in words if w.score and w.dictionary_valid),
            default=0
        )
        if max_len > longest:
            longest = max_len
            longest_unique = True
        elif max_len == longest:
            longest_unique = False

    for (pl_id, pl_name), words in by_player.items():

        # If this player is the only one with words of this length, all
        # words with the longest length get awarded double points
        def effective_scores():
            for w in words:
                score_json = w.score_json()
                if not use_mild_scoring and w.duplicate:
                    score_json['score'] = 0
                if longest_unique and len(w.word) == longest:
                    score_json['score'] *= 2
                    score_json['longest_bonus'] = True
                else:
                    score_json['longest_bonus'] = False
                yield score_json

        yield {
            'player': {'player_id': pl_id, 'name': pl_name},
            'words': sorted(effective_scores(), key=lambda w: w['word'])
        } 


def retrieve_submitted_words(session_id, round_no,
                             scoring_scheme=ScoringScheme.BASIC):
    if scoring_scheme == ScoringScheme.SQL_MILD:
        model = WordWithEffectiveMildScore
    elif scoring_scheme == ScoringScheme.SQL:
        model = WordWithEffectiveScore
    else:
        model = Word

    word_query = model.query.join(model.submission) \
        .join(Submission.player) \
        .filter(Player.session_id == session_id) \
        .filter(Submission.round_no == round_no) \
        .add_columns(Player.id, Player.name)

    by_player = defaultdict(list)

    for w, player_id, player_name in word_query.all():
        by_player[(player_id, player_name)].append(w)

    return by_player


class DictionaryTask(celery.Task, ABC):
    _dicts = None
    _dict_list = None

    @property
    def dictionaries(self):
        if self._dicts is None:
            dirname = app.config['DICTIONARY_DIR']
            self._dicts = boggle_utils.DictionaryServiceProvider(dirname)
            # populate _dict_list too
            self._dict_list = tuple(self._dicts)
        return self._dicts

    @property
    def dicts_available(self):
        # typically, the process calling this function doesn't actually
        #  need the actual dictionaries, just the list is fine
        if self._dict_list is None:
            dirname = app.config['DICTIONARY_DIR']
            self._dict_list = tuple(
                dic_name for dic_name in
                boggle_utils.DictionaryServiceProvider.discover(dirname)
            )
        return self._dict_list


@celery_app.task(base=DictionaryTask)
def trigger_scoring(session_id, round_no, round_seed, dice_config):
    try:
        logger.debug(
            f"Received scoring request for session {session_id}, round {round_no}."
        )
        sess: BoggleSession = BoggleSession.for_update(session_id)

        if sess.round_scored is not None:
            logger.debug(
                f"Scoring is already underway or finished for session {session_id},"
                f" round {round_no}. Exiting early."
            )
            # either we're already done scoring, or a computation is running
            # regardless, we should relinquish the lock on the session table
            db.session.commit()
            return

        # mark score computation as started and commit
        #  to release the for update lock
        sess.round_scored = False
        db.session.commit()

        # run the scoring logic
        by_player = retrieve_submitted_words(session_id, round_no)
        # either there were no submissions, or the session was nixed
        #  in between calls
        if not by_player:
            sess.round_scored = True
            db.session.commit()
            return

        dice = dice_configs[dice_config]
        dims, board = boggle_utils.roll(round_seed, dice_config=dice)

        dictionary = None
        if sess.dictionary is not None:
            try:
                dictionary = trigger_scoring.dictionaries[sess.dictionary]
            except KeyError:
                logger.warning(f"Failed to load dictionary {sess.dictionary}")

        boggle_utils.score_players(
            by_player.values(), board,
            base_scores=app.config['BASE_SCORE_VALUES'],
            dictionary=dictionary
        )

        sess = BoggleSession.for_update(session_id, allow_nonexistent=True)
        if sess is None:
            abort(410, description="Session was killed unexpectedly")
        # this update doesn't involve any cross-table shenanigans,
        #  so bulk_save_objects is safe to use
        db.session.bulk_save_objects(chain(*by_player.values()))
        sess.round_scored = True
        db.session.commit()
        logger.debug(
            f"Finished scoring session {session_id}, round {round_no}."
        )
    except Exception as e:
        # bailing is not a problem, since the next GET request will simply
        #  retrigger the computation if the issue is temporary (e.g. a
        #  database shutdown)
        logger.warning("Encountered failure during score computation", e)
        db.session.rollback()
    finally:
        db.session.remove()
