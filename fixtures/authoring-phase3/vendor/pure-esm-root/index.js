import { normalizeBoardTitle } from '@vellum/fixture-pure-esm-leaf';

export const formatBoardTitle = (value) => `Board: ${normalizeBoardTitle(value)}`;
