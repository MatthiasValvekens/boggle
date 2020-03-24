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
    boggle.app.config['DEFAULT_COUNTDOWN'] = 0

    with boggle.app.test_client() as client:
        with boggle.app.app_context():
            boggle.db.drop_all()
            boggle.init_db()
        yield client


# AQLT
# OLEO
# FDGI
# LHIE
DEFAULT_TESTING_BOARD = boggle_utils.roll(5)


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


SessionData = namedtuple(
    'SessionData',
    ['session_id', 'pepper', 'mgmt_token',
     'session_token', 'manage_url', 'join_url']
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


def create_session(client) -> SessionData:
    with boggle.app.app_context():
        spawn_url = flask.url_for('spawn_session')
    response = client.post(spawn_url)
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
    return SessionData(
        session_id=session_id, pepper=pepper, session_token=session_token,
        mgmt_token=mgmt_token, manage_url=manage_url, join_url=join_url
    )


def create_player_in_session(client, sess: SessionData = None, name='tester') \
        -> GameContext:
    if sess is None:
        sess = create_session(client)
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

    response = client.delete(sess.manage_url)
    assert response.status_code == 204, response.get_json()
    with boggle.app.app_context():
        assert not boggle.db.session.query(exists_q).scalar()


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
    gc = create_player_in_session(client)

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

    words_to_submit = ['AQULGE', 'QLGE', 'ALGEIG', 'DGIEIHLFLO', 'QULGE']

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
    word1, word2, word3, word4 = scored_words

    assert word1['word'] == 'ALGEIG'
    assert word1['score'] == 0
    assert word1['path'] is None
    assert not word1['duplicate']
    assert word1['dictionary_valid']

    # we supplied a version with QU, so it should be returned as such
    assert word2['word'] == 'AQULGE'
    # QU counts as two separate letters for scoring purposes
    assert word2['score'] == 3
    # ... but the path should have length 5
    assert len(word2['path']) == 5
    assert not word2['duplicate']
    assert word2['dictionary_valid']

    assert word3['word'] == 'DGIEIHLFLO'
    assert word3['score'] == 11
    assert len(word3['path']) == 10
    assert not word3['duplicate']
    assert word3['dictionary_valid']

    # if both a Q-version and a non-Q version are submitted,
    # the actual value returned is undefined
    #  this corner case is probably irrelevant in practice, since I doubt
    #  that there are many dictionary words that are valid both with and
    #  without Q
    assert word4['word'] in ('QLGE', 'QULGE')
