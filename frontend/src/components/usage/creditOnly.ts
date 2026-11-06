/**
 * Why cost per token and per request are not simply spend over the Tokens and Requests columns: requests made
 * with the operator's own provider keys (BYOK) report no credit cost, so the backend leaves them out of both sides.
 */
export const CREDIT_ONLY = 'Over credit-billed traffic only: requests made with your own provider keys are left out.';
