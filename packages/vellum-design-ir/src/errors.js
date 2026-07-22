export class DesignIRValidationError extends Error {
    constructor(message, issues = []) {
        super(message);
        this.name = 'DesignIRValidationError';
        this.issues = issues;
    }
}

export class DesignIRConflictError extends Error {
    constructor(message, conflicts = []) {
        super(message);
        this.name = 'DesignIRConflictError';
        this.conflicts = conflicts;
    }
}
