from collections import namedtuple

import pytest
import json
import boggle
import boggle_utils
import flask


@pytest.fixture
def client():
    boggle.app.config['TESTING'] = True
    boggle.app.config['SERVER_NAME'] = 'localhost.localdomain'

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
    ['session_id', 'pepper', 'session_mgmt_token', 'session_token']
)


def request_json(client, method, url, *args, data, headers=None, **kwargs):
    if method not in ('get', 'post', 'put', 'delete'):
        raise ValueError("That's probably not what you meant")

    req_headers = {'content-type': 'application/json'}

    if headers is not None:
        req_headers.update(headers)

    req = getattr(client, method)
    return req(url, *args, data=json.dumps(data), headers=req_headers, **kwargs)


def create_session(client):
    with boggle.app.app_context():
        spawn_url = flask.url_for('spawn_session')
    response = client.post(spawn_url)
    rdata = response.get_json()
    assert response.status_code == 201, rdata
    return SessionData(**rdata)


def test_create_destroy_session(client):
    sess = create_session(client)
    exists_q = boggle.BoggleSession.query.filter(
        boggle.BoggleSession.id == sess.session_id
    ).exists()
    with boggle.app.app_context():
        assert boggle.db.session.query(exists_q).scalar()
        manage_url = flask.url_for(
            'manage_session', session_id=sess.session_id, pepper=sess.pepper,
            mgmt_token=sess.session_mgmt_token
        )
    response = client.get(manage_url)
    rdata = response.get_json()
    assert rdata['players'] == [], rdata['players']
    assert rdata['status'] == boggle.Status.INITIAL

    response = client.delete(manage_url)
    assert response.status_code == 204, response.get_json()
    with boggle.app.app_context():
        assert not boggle.db.session.query(exists_q).scalar()


def test_join_session(client):
    sess = create_session(client)

    with boggle.app.app_context():
        join_url = flask.url_for(
            'session_join', session_id=sess.session_id, pepper=sess.pepper,
            inv_token=sess.session_token
        )
        assert 'join' in join_url, join_url

    # first try posting without supplying a name
    response = client.post(join_url)
    rdata = response.get_json()
    assert response.status_code == 400, rdata
    response = request_json(client, 'post', join_url, data={})
    rdata = response.get_json()
    assert response.status_code == 400, rdata

    # try an actual response
    response = request_json(client, 'post', join_url, data={'name': 'tester'})
    rdata = response.get_json()
    assert response.status_code == 201, rdata
    assert rdata['name'] == 'tester'
    player_id, player_token = rdata['player_id'], rdata['player_token']

    with boggle.app.app_context():
        play_url = flask.url_for(
            'play', session_id=sess.session_id, pepper=sess.pepper,
            player_id=player_id, player_token=player_token
        )
        assert 'play' in play_url, play_url
    response = client.get(play_url)
    rdata = response.get_json()
    assert response.status_code == 200, rdata
    assert rdata['players'][0]['player_id'] == player_id, rdata['players']
    assert rdata['status'] == boggle.Status.INITIAL
