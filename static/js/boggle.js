import * as boggleModel from './boggle-model.js';

/**
 * Boggle configuration parameters.
 *
 * @type {Object}
 * @property {string} apiBaseURL - Base URL for the Boggle API
 * @property {int} heartbeatTimeout - Timeout in milliseconds between state polls.
 */
export const BOGGLE_CONFIG = {
    apiBaseURL: "",
    heartbeatTimeout: 3000,
    emptyTimerString: '-:--'
};


export const boggleController = function () {
    const RoundState = boggleModel.RoundState;

    /** @type {SessionContext} */
    let _sessionContext = null;

    /** @returns {!SessionContext} */
    function sessionContext() {
        if (_sessionContext === null)
            throw "No session context";
        return _sessionContext;
    }


    /** @type {PlayerContext} */
    let _playerContext = null;

    /** @returns {!PlayerContext} */
    function playerContext() {
        if (_playerContext === null)
            throw "No player context";
        return _playerContext;
    }

    function ajaxErrorHandler(response) {
        if (!('responseJSON' in response)) {
            console.log("Unknown error on API call")
        }
        let {responseJSON: {error}, status} = response;
        console.log(`API Error ${status}: ${error}`);
    }

    /**
     * Call the Boggle API.
     * @callback callback
     * @param {!string} method - HTTP method to use
     * @param {!string} endpoint - Endpoint URL (relative to {@link BOGGLE_CONFIG.apiBaseURL})
     * @param {!object} data - Data to send in request body (will be JSONified)
     * @param callback - Response callback
     * @param errorHandler - Error callback
     * @returns {*}
     */
    function callBoggleApi(method, endpoint, data,
                           callback, errorHandler=ajaxErrorHandler) {
        return $.ajax({
            url: BOGGLE_CONFIG.apiBaseURL + endpoint,
            type: method,
            data: JSON.stringify(data),
            contentType: "application/json"
        }).done(callback).fail(errorHandler);
    }

    /**
     * Simpler Boggle API call for GET requests
     * @callback callback
     * @param {!string} endpoint
     * @param callback
     */
    function boggleAPIGet(endpoint, callback) {
        return $.getJSON(BOGGLE_CONFIG.apiBaseURL + endpoint, null, callback);
    }

    /**
     * Join the session specified in the session context.
     * @param {!string} name
     */
    function requestJoin(name) {

        function playerSetupCallback({player_id, player_token, name}) {
            let sess = sessionContext();
            _playerContext = Object.freeze(
                new boggleModel.PlayerContext(sess, player_id, player_token, name)
            );

            if(sess.isManager) {
                $('#manager-controls').show();
                $('#inv-token-display').val(
                    `${sess.sessionId}:${sess.saltToken}:${sess.invToken}`
                );
            }
            $('#start-section').hide();
            $('#game-section').show();

            gameState = new boggleModel.GameState(_playerContext);
            heartbeat();
        }
        return callBoggleApi(
            'post', sessionContext().joinEndpoint,
            {'name': name}, playerSetupCallback
        );
    }

    /** @returns {?string} */
    function retrievePlayerName() {
        const input = $('#player-name-input');
        if(!input.get(0).reportValidity()) {
            input.addClass("is-danger");
            return null;
        }
        input.removeClass("is-danger");
        return input.val();
    }

    function joinExistingSession() {
        const name = retrievePlayerName();
        if(name === null)
            return;

        // parse invitation token
        const invToken = $('#inv-token');
        const match = invToken.val().match(/^(\d+):([0-9a-f]{16}):([0-9a-f]{20})$/);
        if(match === null) {
            invToken.addClass("is-danger");
            $('#inv-token-error').show();
            return;
        }
        _sessionContext = Object.freeze(
            new boggleModel.SessionContext(parseInt(match[1]), match[2], match[3])
        );
        $('#join-session').addClass("is-loading").prop("disabled", true);

        requestJoin(name).fail(function() {
            invToken.addClass("is-danger");
            $('#inv-token-error').show();
            _sessionContext = null;
        }).done(function() {
            invToken.removeClass("is-danger");
            $('#inv-token-error').hide();
        }).always(function(){
            $('#join-session').removeClass("is-loading").prop("disabled", false);
        });
    }

    /**
     * Retrieve a list of dictionaries from the server
     */
    function listDictionaries() {
        $('#spawn-session').addClass("is-loading").prop("disabled", true);
        return boggleAPIGet( '/dictionaries', function({dictionaries}) {
            const selector = $('#dictionary');
            dictionaries.forEach(
                function (dictionary) {
                    selector.append(
                        `<option value="${dictionary}">${dictionary}</option>`
                    );
                }
            );
            // set up the spawn session button
            $('#spawn-session').removeClass("is-loading")
                .click(function(){
                    /** @type {string} */
                    const name = retrievePlayerName();
                    if(name === null || !selector.get(0).reportValidity())
                        return;
                    const dictionary = $('#dictionary option:selected').val();
                    spawnSession(name, dictionary);
                }).prop("disabled", false);
            }
        )
    }

    /**
     * Create a session.
     * @param {!string} playerName
     * @param {?string} dictionary
     */
    function spawnSession(playerName, dictionary=null) {
        _sessionContext = null;

        let data;
        if(dictionary !== null)
            data = {'dictionary': dictionary};
        else
            data = {};
        return callBoggleApi(
            'post', '/session', data,
            function ({session_id, pepper, session_mgmt_token, session_token}) {
                _sessionContext = Object.freeze(
                    new boggleModel.SessionContext(session_id, pepper, session_token, session_mgmt_token)
                );
                requestJoin(playerName);
            }
        );
    }

    /**
     * Toggle the global busy indicator
     * @param {!boolean} busy
     */
    function toggleBusy(busy) {
        if(busy)
            $('#loading-icon').show();
        else
            $('#loading-icon').hide();
    }

    let heartbeatTimer = null;
    /** @type {GameState} */
    let gameState = null;
    /** @type {?int} */
    let timerGoalValue = null;

    function timerControl(goalCallback=null) {
        let timerElement = document.getElementById('timer');
        if(timerGoalValue === null)
            timerElement.innerText = BOGGLE_CONFIG.emptyTimerString;
        let delta = timerGoalValue - (new Date().getTime());
        if(delta <= 0) {
            if(goalCallback !== null)
                goalCallback();
            timerElement.innerText = BOGGLE_CONFIG.emptyTimerString;
            timerGoalValue = null;
            // no need to reschedule the timer
            return;
        }
        let minutes = Math.floor(delta / (1000 * 60));
        let seconds = Math.floor((delta - minutes * 1000 * 60) / 1000);
        timerElement.innerText = `${minutes}:${seconds < 10? '0' : ''}${seconds}`;
        setTimeout(() => timerControl(goalCallback), 1000);
    }

    function advanceRound() {
        let requestData = {'until_start': 8}; //TODO placeholder
        return callBoggleApi('POST', sessionContext().mgmtEndpoint, requestData, function () {
            $('#advance-round').prop("disabled", true);
        })
    }

    function submitWords() {
        if(gameState === null)
            throw "Cannot submit";
        if(gameState.roundSubmitted)
            return;
        const words = $('#words').val().toUpperCase().split(/\s+/);
        let submission = {'round_no': gameState.roundNo, 'words': words};
        callBoggleApi('put', playerContext().playEndpoint, submission, forceRefresh);
        gameState.markSubmitted();
    }


    function heartbeat() {
        if (gameState === null)
            throw "Game not running";

        if (heartbeatTimer !== null) {
            clearTimeout(heartbeatTimer);
            heartbeatTimer = null;
        }

        toggleBusy(true);
        boggleAPIGet(playerContext().playEndpoint, function (response) {
            if (gameState === null) {
                console.log("Game ended while waiting for server response.");
                return;
            }
            let gameStateAdvanced = gameState.updateState(response);
            let status = gameState.status;

            // update the player list
            let currentPlayer = playerContext().playerId;
            let playerListFmtd = gameState.playerList.map(
                ({playerId, name}) =>
                    `<li data-player-id="${playerId}" ${playerId === currentPlayer ? 'class="me"' : ''}>${name}</li>`
            ).join('');
            $('#player-list ul').html(playerListFmtd);


            // update the timer control, if necessary
            let noTimerRunning = timerGoalValue === null;
            switch(status) {
                case RoundState.PRE_START:
                    // count down to start of round
                    timerGoalValue = gameState.roundStart;
                    if(noTimerRunning)
                        timerControl(forceRefresh);
                    break;
                case RoundState.PLAYING:
                    // count down to end of round, and submit scores when timer reaches zero
                    timerGoalValue = gameState.roundEnd;
                    if(noTimerRunning || gameStateAdvanced)
                        timerControl(submitWords);
                    break;
                case RoundState.SCORING:
                    // if we somehow end up killing the round-end timer, make sure we still submit
                    submitWords();
                default:
                    timerGoalValue = null;
            }
            // update status box
            let statusBox = $('#status-box');
            switch(status) {
                case RoundState.INITIAL:
                    statusBox.text('Wachten op startaankondiging...');
                    break;
                case RoundState.PRE_START:
                    statusBox.text('Ronde begint zometeen...');
                    break;
                case RoundState.PLAYING:
                    statusBox.text('Ronde bezig');
                    break;
                case RoundState.SCORING:
                    statusBox.text('Wachten op scores...');
                    break;
                case RoundState.SCORED:
                    statusBox.text('Scores binnen!');
                default:
                    break;
            }

            // update availability of submission textarea
            if(status === RoundState.PLAYING)
                $('#words').prop("disabled", false).val('');
            else
                $('#words').prop("disabled", true);

            // update board etc.
            if (gameStateAdvanced) {
                let boggleGrid = $('#boggle');
                if(status !== RoundState.INITIAL && status !== RoundState.PRE_START) {
                    let boardHTML = gameState.boardState.map(
                        (row) =>
                            `<tr>${row.map((letter) => `<td>${letter}</td>`).join('')}</tr>`
                    ).join('');
                    boggleGrid.html(boardHTML);
                }
            }

            // update admin interface
            if(sessionContext().isManager) {
                let canAdvance = status === RoundState.INITIAL || status === RoundState.SCORED;
                $('#advance-round').prop("disabled", !canAdvance);
            }

            heartbeatTimer = setTimeout(heartbeat, BOGGLE_CONFIG.heartbeatTimeout);
            toggleBusy(false);
        });
    }

    /**
     * Force a heartbeat call, unless one is already happening now
     */
    function forceRefresh() {
        if(heartbeatTimer !== null) {
            clearTimeout(heartbeatTimer);
            heartbeat();
        }
    }

    return {
        listDictionaries: listDictionaries, joinExistingSession: joinExistingSession,
        advanceRound: advanceRound, forceRefresh: forceRefresh
    }
}();


