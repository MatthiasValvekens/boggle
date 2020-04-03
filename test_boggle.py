from collections import namedtuple

import pytest
import json
import boggle
import boggle_utils
import flask


@pytest.fixture
def client():
    boggle.app.config['TESTING'] = True
    boggle.app.config['TESTING_SEED'] = 5
    boggle.app.config['SERVER_NAME'] = 'localhost.localdomain'
    boggle.app.config['DEFAULT_COUNTDOWN_SECONDS'] = 0
    boggle.app.config['DISABLE_ASYNC_SCORING'] = True
    boggle.trigger_scoring._dicts = {
        'testing': boggle_utils.DictionaryServiceProvider.clean_dict(
            ['AQULGE', 'QLGE', 'ALGEIG', 'DGIEìHLFLO', 'QULGE']
        ),
        'testing2': []
    }
    boggle.trigger_scoring._dict_list = ('testing', 'testing2')

    with boggle.app.test_client() as client:
        with boggle.app.app_context():
            boggle.db.drop_all()
            boggle.init_db()
        yield client


INTL_DICE_CONFIG = (
    'ETUKNO', 'EVGTIN', 'DECAMP', 'IELRUW',
    'EHIFSE', 'RECALS', 'ENTDOS', 'OFXRIA',
    'NAVEDZ', 'EIOATA', 'GLENYU', 'BMAQJO',
    'TLIBRA', 'SPULTE', 'AIMSOR', 'ENHRIS'
)

DICE_CONFIG_DEFAULT = 'International'

# AQLT
# OLEO
# FDGI
# LHIE
DIMS, DEFAULT_TESTING_BOARD = boggle_utils.roll(5, dice_config=INTL_DICE_CONFIG)


def tuple_paths(word, board):
    return set(map(tuple, boggle_utils.paths(word, board)))


def test_board_path():
    paths = tuple_paths('ALGE', DEFAULT_TESTING_BOARD)
    alg_path = [(0, 0), (1, 1), (2, 2)]
    assert paths == {(*alg_path, (3, 3)), (*alg_path, (1, 2))}, paths
    paths = tuple_paths('ALGEI', DEFAULT_TESTING_BOARD)
    assert len(paths) == 3, paths
    paths = tuple_paths('ALGEIG', DEFAULT_TESTING_BOARD)
    assert len(paths) == 0
    paths = tuple_paths('DGIEIHLFLO', DEFAULT_TESTING_BOARD)
    assert len(paths) == 1
    paths = tuple_paths('B', DEFAULT_TESTING_BOARD)
    assert len(paths) == 0
    paths = tuple_paths('BLHIE', DEFAULT_TESTING_BOARD)
    assert len(paths) == 0


def test_read_dice():
    def dice_cmp(dice):
        return frozenset(frozenset(x) for x in dice)
    config_list = list(boggle_utils.DiceConfigServiceProvider.discover('dice'))
    assert 'International' in config_list
    assert 'English (new)' in config_list
    assert 'English (classic)' in config_list

    configs = boggle_utils.DiceConfigServiceProvider('dice')
    config_list = list(configs)
    assert 'International' in config_list
    assert 'English (new)' in config_list
    assert 'English (classic)' in config_list

    assert dice_cmp(configs[DICE_CONFIG_DEFAULT]) == dice_cmp(INTL_DICE_CONFIG)


SessionData = namedtuple(
    'SessionData',
    ['session_id', 'pepper', 'mgmt_token',
     'session_token', 'manage_url', 'join_url', 'approve_url']
)

GameContext = namedtuple(
    'GameContext', ['session', 'player_token', 'player_id', 'name', 'play_url']
)


def request_json(client, method, url, *args, data, headers=None, **kwargs):
    if method not in ('get', 'post', 'put', 'delete'):
        raise ValueError("That's probably not what you meant")

    req_headers = {'content-type': 'application/json'}

    if headers is not None:
        req_headers.update(headers)

    req = getattr(client, method)
    return req(url, *args, data=json.dumps(data), headers=req_headers, **kwargs)


def create_session(client, dictionary=None, dice_config=None) -> SessionData:
    with boggle.app.app_context():
        spawn_url = flask.url_for('spawn_session')
    dice_config = dice_config or DICE_CONFIG_DEFAULT
    req_data = {'dice_config': dice_config}
    if dictionary is not None:
        req_data['dictionary'] = dictionary
    response = request_json(client, 'post', spawn_url, data=req_data)
    rdata = response.get_json()
    assert response.status_code == 201, rdata
    session_id = rdata['session_id']
    pepper = rdata['pepper']
    mgmt_token = rdata['session_mgmt_token']
    session_token = rdata['session_token']
    with boggle.app.app_context():
        manage_url = flask.url_for(
            'manage_session', session_id=session_id, pepper=pepper,
            mgmt_token=mgmt_token
        )
        join_url = flask.url_for(
            'session_join', session_id=session_id, pepper=pepper,
            inv_token=session_token
        )
        approve_url = flask.url_for(
            'approve_word', session_id=session_id, pepper=pepper,
            mgmt_token=mgmt_token
        )
    return SessionData(
        session_id=session_id, pepper=pepper, session_token=session_token,
        mgmt_token=mgmt_token, manage_url=manage_url, join_url=join_url,
        approve_url=approve_url
    )


def create_player_in_session(client, sess: SessionData = None, name='tester',
                             **kwargs) -> GameContext:
    if sess is None:
        sess = create_session(client, **kwargs)
    response = request_json(client, 'post', sess.join_url, data={'name': name})
    rdata = response.get_json()
    assert response.status_code == 201, rdata
    assert rdata['name'] == name
    player_id, player_token = rdata['player_id'], rdata['player_token']
    with boggle.app.app_context():
        play_url = flask.url_for(
            'play', session_id=sess.session_id, pepper=sess.pepper,
            player_id=player_id, player_token=player_token
        )
    return GameContext(
        session=sess, player_id=player_id, player_token=player_token,
        name=name, play_url=play_url
    )


def test_options(client):
    response = client.get('/options')
    rdata = response.get_json()
    assert rdata['dictionaries'] == ['testing', 'testing2'], rdata

    with boggle.app.app_context():
        spawn_url = flask.url_for('spawn_session')
    response = request_json(client, 'post', data={}, url=spawn_url)
    sess = boggle.BoggleSession.query.get(response.get_json()['session_id'])
    assert sess.dictionary is None
    assert sess.dice_config == 'International'

    response = request_json(
        client, 'post', data={'dictionary': 'testing', 'dice_config': 'Nederlands'},
        url=spawn_url
    )
    sess = boggle.BoggleSession.query.get(response.get_json()['session_id'])
    assert sess.dictionary == 'testing'
    assert sess.dice_config == 'Nederlands'

    response = request_json(
        client, 'post', data={'dictionary': 'idontexist'}, url=spawn_url
    )
    assert response.status_code == 404, response.get_json()

    # rig things so there is only 1 dictionary
    old_dicts = boggle.trigger_scoring._dict_list
    boggle.trigger_scoring._dict_list = ('testing2',)

    # explicit None request
    response = request_json(
        client, 'post', data={'dictionary': None}, url=spawn_url
    )
    sess = boggle.BoggleSession.query.get(response.get_json()['session_id'])
    assert sess.dictionary is None

    # empty JSON data -> default
    response = request_json(client, 'post', data={}, url=spawn_url)
    sess = boggle.BoggleSession.query.get(response.get_json()['session_id'])
    assert sess.dictionary == 'testing2'

    # no JSON data -> default
    response = client.post(spawn_url)
    sess = boggle.BoggleSession.query.get(response.get_json()['session_id'])
    assert sess.dictionary == 'testing2'
    boggle.trigger_scoring._dict_list = old_dicts


def test_create_destroy_session(client):
    sess = create_session(client)
    exists_q = boggle.BoggleSession.query.filter(
        boggle.BoggleSession.id == sess.session_id
    ).exists()
    with boggle.app.app_context():
        assert boggle.db.session.query(exists_q).scalar()
    response = client.get(sess.manage_url)
    rdata = response.get_json()
    assert rdata['players'] == [], rdata['players']
    assert rdata['status'] == boggle.Status.INITIAL

    # we shouldn't be able to start a game without first adding players
    response = client.post(sess.manage_url)
    assert response.status_code == 409, response.get_json()

    response = client.delete(sess.manage_url)
    assert response.status_code == 204, response.get_json()
    with boggle.app.app_context():
        assert not boggle.db.session.query(exists_q).scalar()

    # ... and we shouldn't be able to operate on the session
    # after it's been disposed
    response = client.post(sess.manage_url)
    assert response.status_code == 410, response.get_json()
    response = client.get(sess.manage_url)
    assert response.status_code == 410, response.get_json()


def test_wrong_mgmt_token(client):
    sess = create_session(client)
    with boggle.app.app_context():
        manage_url = flask.url_for(
            'manage_session', session_id=sess.session_id, pepper=sess.pepper,
            mgmt_token='deadbeef'
        )
    response = client.delete(manage_url)
    assert response.status_code == 403, response.get_json()


def test_join_session(client):
    sess = create_session(client)

    # first try posting without supplying a name
    response = client.post(sess.join_url)
    rdata = response.get_json()
    assert response.status_code == 400, rdata
    response = request_json(client, 'post', sess.join_url, data={})
    rdata = response.get_json()
    assert response.status_code == 400, rdata

    # try an actual response
    gc = create_player_in_session(client, sess)
    response = client.get(gc.play_url)
    rdata = response.get_json()
    assert response.status_code == 200, rdata
    assert rdata['players'][0]['player_id'] == gc.player_id, rdata['players']
    assert rdata['status'] == boggle.Status.INITIAL

    # this should fail since the game hasn't started yet
    response = client.put(gc.play_url)
    assert response.status_code == 409

    # try leaving
    response = client.delete(gc.play_url)
    assert response.status_code == 204
    # GET should still work
    response = client.get(gc.play_url)
    assert response.status_code == 200
    # ... but PUT shouldn't: note the difference in status code
    response = client.put(gc.play_url)
    assert response.status_code == 410


def test_wrong_player_token(client):
    sess = create_session(client)
    with boggle.app.app_context():
        play_url = flask.url_for(
            'play', session_id=sess.session_id, pepper=sess.pepper,
            player_id=28, player_token='deadbeef'
        )

    response = request_json(
        client, 'put', play_url, data={'round_no': 1, 'words': []}
    )
    assert response.status_code == 403, response.get_json()

    # try again with a real player id
    gc = create_player_in_session(client, sess)
    with boggle.app.app_context():
        play_url = flask.url_for(
            'play', session_id=sess.session_id, pepper=sess.pepper,
            player_id=gc.player_id, player_token='deadbeef'
        )

    response = request_json(
        client, 'put', play_url, data={'round_no': 1, 'words': []}
    )
    assert response.status_code == 403, response.get_json()


def test_single_player_scenario(client):
    gc = create_player_in_session(client, dictionary='testing')

    # start the session
    response = client.post(gc.session.manage_url)
    assert response.status_code == 200
    round_no = response.get_json()['round_no']
    assert round_no == 1

    pending_q = boggle.BoggleSession._submissions_pending(
        gc.session.session_id, round_no
    )
    with boggle.app.app_context():
        # check that submissions are pending now
        assert boggle.db.session.query(pending_q).scalar()

    # verify that the round is underway
    response = client.get(gc.play_url)
    rdata = response.get_json()
    assert response.status_code == 200, rdata
    assert rdata['status'] == boggle.Status.PLAYING
    assert 'board' in rdata

    words_to_submit = [
        'AQULGE', 'QLGE', 'ALGEIG', 'DGIÉÎHLFLO', 'QULGE', 'TLEGI'
    ]

    # first, attempt a submission for the wrong round
    response = request_json(
        client, 'put', gc.play_url, data={
            'round_no': 27, 'words': words_to_submit
        }
    )
    assert response.status_code == 409

    response = request_json(
        client, 'put', gc.play_url, data={
            'round_no': round_no, 'words': words_to_submit
        }
    )
    assert response.status_code == 201

    # run a specific exists() for the submission we just made
    exists_q = boggle.db.session.query(boggle.Submission).filter(
        boggle.Submission.player_id == gc.player_id,
        boggle.Submission.round_no == round_no
    ).exists()
    with boggle.app.app_context():
        assert boggle.db.session.query(exists_q).scalar()
        # check that submissions are no longer pending
        assert boggle.db.session.query(~pending_q).scalar()

    # trigger scoring by making a GET request
    response = client.get(gc.play_url)
    rdata = response.get_json()
    assert response.status_code == 200, rdata
    assert rdata['status'] == boggle.Status.SCORED
    score_data, = rdata['scores']
    assert score_data['player'] == {'player_id': gc.player_id, 'name': gc.name}
    scored_words = score_data['words']
    # the original submission contained two words that are equivalent in Boggle
    #  so we should get back 3 items, in alphabetical order
    word1, word2, word3, word4, word5 = scored_words

    # test this one first, easier to debug this way (since this guy being
    #  marked as invalid changes the word with the highest score)
    assert word3['word'] == 'DGIEIHLFLO'
    assert len(word3['path']) == 10
    assert not word3['duplicate']
    assert word3['dictionary_valid']
    assert word3['score'] == 11 * 2  # double score bonus

    assert word1['word'] == 'ALGEIG'
    assert word1['path'] is None
    assert not word1['duplicate']
    assert word1['dictionary_valid']
    assert word1['score'] == 0

    # we supplied a version with QU, so it should be returned as such
    assert word2['word'] == 'AQULGE'
    assert not word2['duplicate']
    assert word2['dictionary_valid']
    # QU counts as two separate letters for scoring purposes
    assert word2['score'] == 3
    # ... but the path should have length 5
    assert len(word2['path']) == 5

    # if both a Q-version and a non-Q version are submitted,
    # the actual value returned is undefined
    #  this corner case is probably irrelevant in practice, since I doubt
    #  that there are many dictionary words that are valid both with and
    #  without Q
    assert word4['word'] in ('QLGE', 'QULGE')

    assert word5['word'] == 'TLEGI'
    assert word5['path'] is not None
    assert not word5['duplicate']
    assert not word5['dictionary_valid']
    assert word5['score'] == 0

    response = request_json(
        client, 'put', gc.session.approve_url, data={'words': ['TleGi']}
    )
    rdata = response.get_json()
    assert response.status_code == 200, rdata
    # check validity flag
    assert rdata['scores'][0]['words'][4]['dictionary_valid']


def test_double_submission(client):
    gc = create_player_in_session(client)

    def attempt_submit(words, round_no):
        resp = request_json(
            client, 'put', gc.play_url,
            data={'round_no': round_no, 'words': words}
        )
        assert resp.status_code == 201

        # no double submissions?
        resp = request_json(
            client, 'put', gc.play_url,
            data={'round_no': round_no, 'words': words}
        )
        assert resp.status_code == 409

    # start the session
    response = client.post(gc.session.manage_url)
    assert response.status_code == 200

    # attempt 1: normal submission
    attempt_submit(
        ['AQULGE', 'ALGEIG', 'DGIEIHLFLO'], response.get_json()['round_no']
    )
    # attempt 2: empty submission (in a new round)
    response = client.post(gc.session.manage_url)
    assert response.status_code == 200
    attempt_submit([], response.get_json()['round_no'])


def test_two_player_scenario(client):
    sess = create_session(client)
    gc1 = create_player_in_session(client, sess, name='tester1')
    gc2 = create_player_in_session(client, sess, name='tester2')

    player1_words = ['AQULGE', 'ALGEIG', 'DGIEIHL']
    player2_words = ['AQULGE', 'ALGEIG', 'DGIEIHLFOLEO']

    # start the session
    response = client.post(sess.manage_url)
    assert response.status_code == 200
    round_no = response.get_json()['round_no']
    assert round_no == 1

    def do_submit(play_url, words, expected_status):
        resp = request_json(
            client, 'put', play_url, data={'round_no': round_no, 'words': words}
        )
        assert resp.status_code == 201

        resp = client.get(play_url)
        assert resp.status_code == 200
        resp_json = resp.get_json()
        assert resp_json['status'] == expected_status
        return resp_json

    do_submit(gc1.play_url, player1_words, boggle.Status.PLAYING)
    rdata = do_submit(gc2.play_url, player2_words, boggle.Status.SCORED)
    score_data1, score_data2 = sorted(
        rdata['scores'], key=lambda sd: sd['player']['player_id']
    )
    p1w1, p1w2, p1w3 = score_data1['words']
    p2w1, p2w2, p2w3 = score_data2['words']
    # ALGEIG: invalid + duplicate
    assert p1w1 == p2w1
    assert p1w1['duplicate']
    assert p1w1['score'] == 0
    assert not p1w1['path']

    # AQULGE: valid + duplicate
    assert p1w2 == p2w2
    assert p1w2['duplicate']
    assert p1w2['score'] == 0
    # path should be reported properly, even though this is a duplicate
    assert p1w2['path']

    assert p1w3['word'] == 'DGIEIHL'
    assert p2w3['word'] == 'DGIEIHLFOLEO'
    assert not p1w3['duplicate'] and not p2w3['duplicate']
    assert p1w3['score'] == 5
    assert p2w3['score'] == 11 * 2
    assert p1w3['path'] and p2w3['path']
