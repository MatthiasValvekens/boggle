import * as boggleModel from './boggle-model.js';

/**
 * Boggle configuration parameters.
 *
 * @type {{apiBaseURL: string}} - Base URL for the API.
 */
export const BOGGLE_CONFIG = {
    apiBaseURL: "",
};


export const boggleController = function () {
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

    function ajaxErrorHandler(response, textStatus, errorThrown) {
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
            _playerContext = Object.freeze(
                new boggleModel.PlayerContext(sessionContext(), player_id, player_token, name)
            );

            if(sessionContext().isManager) {
                // TODO activate admin UI
            }
            $('#start-section').hide();
            $('#game-section').show();

            toggleBusy(true);
            // TODO replace registration UI with game UI
            // TODO set up session polling task
        }
        return callBoggleApi(
            'post', sessionContext().joinEndpoint,
            {'name': name}, playerSetupCallback
        );
    }

    /** @returns {string} */
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


    return {
        listDictionaries: listDictionaries, joinExistingSession: joinExistingSession,
    }
}();


