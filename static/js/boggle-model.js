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

    get name() {
        return this._name;
    }
}

