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
        // add a fudge factor of half a second to mitigate timing issues with the server
        let delta = timerGoalValue + 500 - (new Date().getTime());
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
        // clean up word approval UI
        $('#dict-invalid .score').off('click');
        $('#approve-button').remove();
        $('#dict-invalid').removeAttr('id');

        let requestData = {'until_start': parseInt($('#round-announce-countdown').val())};
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
                    // count down to start of round + fudge factor
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
            $('#words-container').toggle(status === RoundState.PLAYING);
            if(gameStateAdvanced)
                $('#words').val('');

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

            let manager = sessionContext().isManager;
            // update scores
            // FIXME do this more cleverly, without redrawing the entire thing every couple seconds
            //  THat would also remove the need for the manager-specific hack
            if(status === RoundState.SCORED && (!manager || gameStateAdvanced)) {
                formatScores(gameState.scores);
                $('#score-section').show();
            }

            // update admin interface
            if(manager) {
                let canAdvance = status === RoundState.INITIAL || status === RoundState.SCORED;
                $('#advance-round').prop("disabled", !canAdvance);
            }

            heartbeatTimer = setTimeout(heartbeat, BOGGLE_CONFIG.heartbeatTimeout);
            toggleBusy(false);
        });
    }

    function approveWords() {
        let requestData = {
            words: $('#dict-invalid .score .approved').toArray().map((el) => el.innerText)
        };
        let endpoint = sessionContext().mgmtEndpoint + '/approve_word';
        return callBoggleApi('put', endpoint, requestData, function({scores}) {
            gameState.updateScores(scores);
            formatScores(gameState.scores);
        });
    }

    function approveSelectHandler() {
        let targ = $('span', this);
        if(targ.hasClass('approved')) {
            targ.addClass("is-danger");
            targ.removeClass("is-success approved");
        } else {
            targ.removeClass("is-danger");
            targ.addClass("is-success approved");
        }
        let candidates = $('#dict-invalid .score .approved').length;
        // only enable approve-button if there are words to approve
        $('#approve-button').prop("disabled", !candidates);
    }

    /** @param {int[][]} path */
    function highlightPath(path) {
        $('#boggle td').removeAttr('data-order');
        for(const [ix, [row, col]] of path.entries()) {
            let cell = $(`#boggle tr:nth-child(${row + 1}) td:nth-child(${col + 1})`);
            cell.attr('data-order', ix + 1);
        }
    }

    // set path reveal onHover, de-hover clears the path display
    function highlightPathHandler(event) {
        if(event.type === 'mouseenter') {
            let pathData = $(this).attr('data-path');
            highlightPath(JSON.parse(pathData));
        } else highlightPath([]);
    }

    const approveButton = `
        <button class="button is-primary is-small" id="approve-button" disabled>
            Goedkeuren
        </button>`;
    /**
     * @param {RoundScoreSummary} roundScoreSummary
     */
    function formatScores(roundScoreSummary) {

        function fmtBad(str, colClass) {
            return `<div class="control score"><div class="tags has-addons" translate="no">
                        <span class="tag ${colClass}">${str}</span>
                    </div></div>`;
        }

        /**
         * @param {WordScore} wordScore
         */
        function fmtPathAttr(wordScore) {
            return wordScore.in_grid ? `data-path='${JSON.stringify(wordScore.path)}'` : '';
        }

        /** @param {WordScore} wordScore */
        function fmtWord(wordScore) {
            if(wordScore.score > 0) {
                return `<div class="control score" ${fmtPathAttr(wordScore)}><div class="tags has-addons" translate="no">
                        <span class="tag${wordScore.longest_bonus ? ' is-warning' : ''}">${wordScore.word}</span>
                        <span class="tag is-success">${wordScore.score}</span>
                    </div></div>`;
            } else if(wordScore.in_grid) {
                return `<div class="control score" ${fmtPathAttr(wordScore)}><div class="tags has-addons" translate="no">
                        <span class="tag is-danger">${wordScore.word}</span>
                    </div></div>`;
            } else {
                return fmtBad(wordScore.word, "is-dark");
            }
        }

        function fmtPlayer({playerId, name}) {
            let {total, words} = roundScoreSummary.wordsByPlayer(playerId);
            let wordList = words.map(fmtWord).join('');
            return `<div class="score-list-container"> <div class="field is-grouped is-grouped-multiline"  data-header="${name} (${total})">${wordList}</div></div>`
        }
        let scoreId = `scores-round-${roundScoreSummary.roundNo}`;
        $(`#${scoreId}`).remove();

        let duplicates = '';
        if(roundScoreSummary.duplicates.size) {
            duplicates = `<div class="score-list-container">
                <div class="field is-grouped is-grouped-multiline" data-header="Duplicaten">
                    ${Array.from(roundScoreSummary.duplicates).map(
                (x) => fmtBad(x, "is-danger")).join('')}
                </div>
            </div>`;
        }

        let invalidWords = '';
        if(roundScoreSummary.dictInvalidWords.size) {
            let manager = sessionContext().isManager;
            let coreFmt = Array.from(roundScoreSummary.dictInvalidWords)
                            .map((x) => fmtBad(x, "is-danger")).join('');
            invalidWords = `<div class="score-list-container" ${manager ? 'id="dict-invalid"' : ''}>
                <div class="field is-grouped is-grouped-multiline" data-header="Niet in woordenboek">${coreFmt}</div>
            </div>${manager ? approveButton: ''}`;
        }

        let structure = `
            <article class="media" id="${scoreId}">
                <figure class="media-left">
                    <p class="image is-64x64">
                        <span class="fas fa-trophy fa-3x"></span>
                    </p>
                </figure>
                <div class="media-content">
                    <div class="content">
                    <p>
                        <strong>Ronde ${roundScoreSummary.roundNo}</strong><br>
                    </p>
                    <div class="player-scores">
                    ${gameState.playerList.map(fmtPlayer).join('')}
                    </div>
                    <hr>${duplicates ? duplicates : ''} ${invalidWords ? invalidWords : ''}
                    </div>
                </div>
            </article> `;
        $('#score-container').prepend(structure);

        // add approval toggle
        if(sessionContext().isManager) {
            $('#dict-invalid').on("click", ".score", approveSelectHandler);
            $('#approve-button').click(approveWords);
        }

        $('.player-scores').on('mouseenter mouseleave', '.score[data-path]', highlightPathHandler);
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
        advanceRound: advanceRound
    }
}();


