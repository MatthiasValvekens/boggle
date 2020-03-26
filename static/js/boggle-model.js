export const RoundState = Object.freeze({
    INITIAL: 0, PRE_START: 1,
    PLAYING: 2, SCORING: 3,
    SCORED: 4
});

export class SessionContext {
    /**
     * Boggle session context, including (optional) access to the management API.
     * @param {!int} sessionId
     * @param {!string} saltToken
     * @param {!string} invToken
     * @param {?string=null} mgmtToken
     */
    constructor(sessionId, saltToken, invToken, mgmtToken=null) {
        this.sessionId = sessionId;
        this.saltToken = saltToken;
        this.invToken = invToken;
        this.mgmtToken = mgmtToken;
    }

    get endpointBase() {
        return `/session/${this.sessionId}/${this.saltToken}`;
    }

    get joinEndpoint() {
        return `${this.endpointBase}/join/${this.invToken}`;
    }

    get isManager() {
        return this.mgmtToken !== null;
    }

    get mgmtEndpoint() {
        if(!this.isManager)
            throw "Management token not present";
        return `${this.endpointBase}/manage/${this.mgmtToken}`;
    }

}

export class PlayerContext {
    /**
     * Boggle player context.
     * @param {!SessionContext} sessionContext
     * @param {int} playerId
     * @param {string} playerToken
     * @param {string} name
     */
    constructor(sessionContext, playerId, playerToken, name) {
        this.playerId = playerId;
        this.playerToken = playerToken;
        this._name = name;
        this.sessionContext = sessionContext;
    }

    get playEndpoint() {
        return `${this.sessionContext.endpointBase}/play/${this.playerId}/${this.playerToken}`;
    }

    /**
     * @returns {string}
     */
    get name() {
        return this._name;
    }
}

/**
 * @typedef {Object} Player
 * @property {string} name
 * @property {int} playerId
 */

/**
 * @typedef {Object} BoardSpec
 * @property {int} rows
 * @property {int} cols
 * @property {string[][]} dice
 */

/**
 * @typedef {Object} ServerGameState
 * @property {string} created - Time when session was created
 * @property {{name: string, player_id: int}[]} players - List of players
 * @property {int} status - State of the session
 * @property {string} [round_start] - Start of current round
 * @property {string} [round_end] - End of current round
 * @property {BoardSpec} [board] - State of the current Boggle board
 * @property {Object} [scores] - Scoring object TODO
 */

export class GameState {
    /**
     * @param {PlayerContext} playerContext
     */
    constructor(playerContext) {
        this._status = RoundState.INITIAL;
        this._roundNo = 0;
        this._roundSubmitted = false;
        this._roundStart = null;
        this._roundEnd = null;
        this._boardCols = null;
        this._boardRows = null;
        this._boardState = null;
        /** @type {Player[]} */
        this._playerList = [{name: playerContext.name, playerId: playerContext.playerId}];
    }

    /**
     * Update the game state with a response from the server.
     * @param {ServerGameState} serverUpdate
     */
    updateState(serverUpdate) {
        let { status, roundNo } = serverUpdate;
        this._playerList = serverUpdate.players.map(
            ({name, player_id}) => ({name: name, playerId: player_id})
        );
        let gameStateAdvanced = this._status !== status;
        if(this._roundNo !== roundNo) {
            this._roundSubmitted = false;
            gameStateAdvanced = true;
        }
        this._roundNo = roundNo;
        console.log('STATUS ' + status);
        switch(status) {
            case RoundState.SCORED:
                // TODO update score data
            case RoundState.SCORING:
            case RoundState.PLAYING:
                this._boardCols = serverUpdate.board.cols;
                this._boardRows = serverUpdate.board.rows;
                this._boardState = serverUpdate.board.dice;
                console.log("END" + serverUpdate.round_start);
                this._roundEnd = moment.utc(serverUpdate.round_end).valueOf();
            case RoundState.PRE_START:
                console.log("START" + serverUpdate.round_start + " " + moment.utc(serverUpdate.round_start));
                this._roundStart = moment.utc(serverUpdate.round_start).valueOf();
            case RoundState.INITIAL:
                break;
        }
        this._status = status;
        return gameStateAdvanced;
    }

    /**
     * @returns {Player[]}
     */
    get playerList() {
        return this._playerList;
    }

    /**
     * @returns {?string[][]}
     */
    get boardState() {
        return this._boardState;
    }

    /**
     * @returns {int}
     */
    get boardRows() {
        return this._boardRows;
    }

    /**
     * @returns {int}
     */
    get boardCols() {
        return this._boardCols;
    }

    /**
     * @returns {int}
     */
    get status() {
        return this._status;
    }

    /**
     * @returns {int}
     */
    get roundNo() {
        return this._roundNo;
    }

    /**
     * @returns {int}
     */
    get roundStart() {
        return this._roundStart;
    }

    /**
     * @returns {int}
     */
    get roundEnd() {
        return this._roundEnd;
    }

    /**
     * @returns {boolean}
     */
    get roundSubmitted() {
        return this._roundSubmitted;
    }

    markSubmitted() {
        this._roundSubmitted = true;
    }
}
