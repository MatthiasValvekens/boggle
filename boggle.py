import secrets
import hashlib
import hmac
import json
from collections import defaultdict
from itertools import chain

from datetime import datetime, timedelta
from enum import IntEnum

from flask import Flask, abort, request, jsonify
from sqlalchemy.exc import IntegrityError

from sqlalchemy.orm import validates
from sqlalchemy import UniqueConstraint
from flask_sqlalchemy import SQLAlchemy

import boggle_utils


app = Flask(__name__)


class DefaultConfig:
    SQLALCHEMY_DATABASE_URI = 'postgresql://boggle@localhost:5432/boggle'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    BOARD_ROWS = 4
    BOARD_COLS = 4
    ROUND_DURATION_MINUTES = 3
    GRACE_PERIOD_SECONDS = 10
    DEFAULT_COUNTDOWN_SECONDS = 15
    DICE_CONFIG = boggle_utils.DICE_CONFIG


app.config.from_object(DefaultConfig)
app.config['SECRET_KEY'] = server_key = secrets.token_bytes(32)
app.config.from_envvar('BOGGLE_CONFIG', silent=True)
db = SQLAlchemy(app)

DATE_FORMAT_STR = '%Y-%m-%d %H:%M:%s'
MAX_NAME_LENGTH = 250

if __name__ == '__main__':
    app.run()


def json_err_handler(error_code):
    return lambda e: (jsonify(error=str(e)), error_code)


for err in (400, 403, 404, 409):
    app.register_error_handler(err, json_err_handler(err))


@app.before_first_request
def init_db():
    # create tables as necessary
    db.create_all()
    con = db.engine
    with con.begin():
        # truncate all sessions on every restart
        con.execute('TRUNCATE boggle_session RESTART IDENTITY CASCADE;')


class BoggleSession(db.Model):
    __tablename__ = 'boggle_session'

    id = db.Column(db.Integer, primary_key=True)
    created = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

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

    def __repr__(self):
        fmt_ts = self.created.datetime.now().strftime(DATE_FORMAT_STR)
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


class Word(db.Model):
    __tablename__ = 'word'
    __table_args__ = (
        UniqueConstraint('submission_id', 'word'),
    )

    id = db.Column(db.Integer, primary_key=True)
    submission_id = db.Column(
        db.Integer, db.ForeignKey('submission.id', ondelete="cascade"),
        nullable=False
    )
    submission = db.relationship(
        Submission, backref=db.backref('words')
    ) 
    word = db.Column(db.String(20), nullable=False)

    score = db.Column(db.Integer, nullable=True)
    duplicate = db.Column(db.Boolean, nullable=True)
    dictionary_valid = db.Column(db.Boolean, nullable=True)
    # we store this as text, since it's only needed for UI feedback
    path_array = db.Column(db.Text, nullable=True)

    def score_json(self):
        return {
            'score': self.score,
            'duplicate': self.duplicate,
            'dictionary_valid': self.dictionary_valid,
            # this is a bit silly, since we'll just reencode
            #  it right away, but it's the most straightforward
            #  way to go about it
            'path_array': json.loads(self.path_array)
        }

    # noinspection PyUnusedLocal
    @validates('word')
    def to_uppercase(self, key, value):
        return value.upper()


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


@app.route('/session', methods=['POST'])
def spawn_session():
    new_session = BoggleSession()
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


@app.route(mgmt_url, methods=['GET', 'POST', 'DELETE'])
def manage_session(session_id, pepper, mgmt_token):
    check_mgmt_token(session_id, pepper, mgmt_token)

    if request.method == 'GET':
        return session_state(session_id, pepper)

    if request.method == 'DELETE':
        BoggleSession.query.filter(BoggleSession.id == session_id).delete()
        db.session.commit()
        return {}, 204

    if request.method == 'POST':
        # prepare a new round
        sess: BoggleSession = BoggleSession.for_update(session_id)
        # if a scoring computation is running right now, this isn't allowed
        #  note that sess.round_scored being None is not an issue
        if sess.round_scored is False:
            return abort(409, "Round cannot be advanced mid-scoring")
        sess.round_scored = None
        # TODO: make this customisable in the request
        sess.round_start = datetime.utcnow() + timedelta(
            seconds=app.config.DEFAULT_COUNTDOWN
        )
        sess.round_no += 1
        db.session.commit()
        info = {'round_no': sess.round_no, 'round_start': sess.round_start}
        return info, 201


@app.route(session_url_base + '/join/<inv_token>', methods=['POST'])
def session_join(session_id, pepper, inv_token):
    true_token = gen_session_inv_token(session_id, pepper)
    if inv_token != true_token:
        abort(403, description="Bad session token")

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
    sess = BoggleSession.query.get_or_404(session_id)
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
    round_seed = (str(round_no) + pepper).encode('ascii') + server_key
    duration = timedelta(minutes=app.config.ROUND_DURATION_MINUTES)
    round_end = round_start + duration
    now = datetime.utcnow()
    response['round_start'] = round_start.strftime(DATE_FORMAT_STR)
    response['round_end'] = round_end.strftime(DATE_FORMAT_STR)
    response['round_no'] = round_no

    if now < round_start:
        response['status'] = Status.PRE_START
        return response

    cols = app.config.BOARD_COLS
    rows = app.config.BOARD_ROWS
    board = boggle_utils.roll(
        round_seed, board_dims=(rows, cols), dice_config=app.config.DICE_CONFIG
    )
    response['board'] = {'cols': cols, 'rows': rows, 'dice': board}

    if now < round_end:
        response['status'] = Status.PLAYING
        return response

    grace_period = timedelta(seconds=app.config.GRACE_PERIOD_SECONDS)
    if now <= round_end + grace_period:
        # check if everyone submitted
        #  i.e. check if there exists a player in the session
        #  who hasn't submitted yet
        still_waiting = Player.query \
            .filter(Player.session_id == session_id) \
            .filter(
                ~Submission.query.where(
                    (Submission.player_id == Player.id)
                    & (Submission.round_no == round_no)
                ).exists()
            ).exists()
        can_score = db.session.query(still_waiting).scalar()
    else:
        # grace period is over, so we force scoring if necessary
        can_score = True

    if can_score:
        scores = trigger_scoring(session_id, round_no, board)
        if scores is not None:
            response['status'] = Status.SCORED
            response['scores'] = scores
            return response

    response['status'] = Status.SCORING
    return response


@app.route(play_url, methods=['GET', 'PUT'])
def play(session_id, pepper, player_id, player_token):
    check_player_token(session_id, pepper, player_id, player_token)
    if request.method == 'GET':
        return session_state(session_id, pepper)

    sess = BoggleSession.for_update(session_id, allow_nonexistent=True)
    current_player = Player.query.get_or_404(player_id)
    now = datetime.utcnow()
    if sess is None:
        return abort(404)

    round_start = sess.round_start
    if round_start is None:
        return abort(409, description="Round not started")

    round_no = sess.round_no
    deadline = round_start + timedelta(
        minutes=app.config.ROUND_DURATION_MINUTES,
        seconds=app.config.GRACE_PERIOD_SECONDS
    )
    if sess.round_scored is not None or now > deadline:
        return abort(409, description="Round already ended")

    submission_json = request.get_json()
    if submission_json is None:
        return abort(400, description="Malformed submission data")
    try:
        words = submission_json['words']
        round_no_supplied = submission_json['round_no_supplied']
    except KeyError:
        return abort(400, desription="Submission not properly structured")
    if round_no != round_no_supplied:
        errmsg = "Wrong round %d, currently round %d" % (
            round_no_supplied, round_no
        )
        return abort(409, description=errmsg)
    submission_obj = Submission(round_no=round_no)
    current_player.append(submission_obj)
    for word in words:
        # TODO can this be done in one append()?
        current_player.submissions.append(Word(word=word))
    try:
        db.session.commit()
    except IntegrityError:
        # due to row locking, a session kill
        #  cannot have occurred in the meantime
        db.session.rollback()
        return abort(409, description="You can only submit once")

    return {}, 201


def words_by_player(word_query):
    by_player = defaultdict(list)

    for w, player_id, player_name in word_query.all():
        by_player[(player_id, player_name)].append(w)

    return by_player


def format_scores(by_player): 
    for (pl_id, pl_name), words in by_player.items():
        yield {
            'player_id': pl_id,
            'name': pl_name,
            'words': [w.score_json() for w in words]
        } 


# FIXME should use sth like Celery with distributed locking
# TODO dictionary support
def trigger_scoring(session_id, round_no, board):
    # we re-query the session to mitigate race conditions
    #  on round_scored
    sess = BoggleSession.for_update(session_id)
    word_query = Word.query\
        .join(Word.submission)\
        .join(Submission.player)\
        .filter(Player.session_id == session_id)\
        .filter(Submission.round_no == round_no)\
        .add_columns(Player.id, Player.name)

    if sess.round_scored is not None:
        # either we're already done scoring, or a computation is running
        # regardless, we should relinquish the lock on the session table
        db.session.commit()
        if sess.round_scored:
            return list(format_scores(words_by_player(word_query)))
        else:
            # scores not available yet
            return

    # mark score computation as started and commit
    #  to release the for update lock
    sess.round_scored = False
    db.session.commit()

    # run the scoring logic
    by_player = words_by_player(word_query)
    # either there were no submissions, or the session was nixed
    #  in between calls
    if not by_player:
        return {}
    boggle_utils.score_players(by_player.values(), board)

    # TODO how necessary is it to run this select for update [...]
    #  again?
    sess = BoggleSession.for_update(session_id)
    # this update doesn't involve any cross-table shenanigans,
    #  so bulk_save_objects is safe to use
    db.session.bulk_save_objects(chain(*by_player.values()))
    sess.round_scored = True
    db.session.commit()
    return list(format_scores(by_player))
