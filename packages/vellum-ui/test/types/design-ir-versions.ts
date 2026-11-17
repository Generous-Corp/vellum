import {
    DESIGN_IR_VERSIONS,
    normalizeImport,
    requiredDesignIRVersion,
    type DesignIR,
    type ImportSourceModel,
} from '../../../vellum-design-ir/src/index.js';

type Versions = (typeof DESIGN_IR_VERSIONS)[number];
type Same<A, B> = [A] extends [B] ? ([B] extends [A] ? true : false) : false;

const required: Same<ReturnType<typeof requiredDesignIRVersion>, Versions> = true;
const documented: Same<DesignIR['schemaVersion'], Versions> = true;
const requested: Same<
    NonNullable<NonNullable<Parameters<typeof normalizeImport>[1]>['schemaVersion']>,
    Versions
> = true;
const plots: ReturnType<typeof requiredDesignIRVersion> = 5;
const vector: NonNullable<Parameters<typeof normalizeImport>[1]>['schemaVersion'] = 6;

export function normalizeAtLatest(input: ImportSourceModel): DesignIR {
    return normalizeImport(input, { schemaVersion: 6 });
}

export { documented, plots, required, requested, vector };
