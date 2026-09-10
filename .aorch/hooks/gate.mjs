// Korean patterns must not use \b: JavaScript word boundaries are based on
// [A-Za-z0-9_], so \b never matches adjacent to Hangul and would make these
// alternations dead code. Substring matching fits Korean agglutination.
const HIGH_RISK_PATTERNS = [
  /\b(production|prod|deploy|release|publish|push|merge|tag)\b/i,
  /\b(database|schema|migration|migrate|drop|truncate|delete data)\b/i,
  /\b(auth|authentication|authorization|permission|credential|secret|security)\b/i,
  // Bare 'order'/'position' over-trigger on everyday English ("in order to",
  // "cursor position"); require trading context around them.
  /\b(payment|billing|financial|trading|trade|risk limit|kill switch)\b/i,
  /\b(place|cancel|execute|submit|amend)\s+(an?\s+|the\s+)?orders?\b|\border\s+(book|entry|execution|management)\b/i,
  /\bposition\s+(limit|sizing)\b|\b(open|close)\s+(a\s+|the\s+)?position\b/i,
  /(운영|배포|릴리스|마이그레이션|스키마|인증|권한|보안|결제|금융|주문|포지션|리스크|실거래|삭제)/
];

const DEVELOPMENT_PATTERNS = [
  /\b(implement|build|create|add|change|modify|edit|fix|debug|refactor|test|upgrade|install|configure|review code)\b/i,
  /(구현|만들|추가|변경|수정|고쳐|디버그|리팩터링|테스트|업그레이드|설치|설정|코드 리뷰)/
];

// Destructive imperatives veto any read-only demotion: "drop the users table
// and show me what remains" must not classify low/open because of "show".
const DESTRUCTIVE_PATTERNS = [
  /\b(drop|truncate|wipe|destroy|delete)\b/i,
  /(삭제|드랍|초기화)/
];

const EXPLICIT_READ_ONLY_PATTERNS = [
  /\b(do not|don't|without)\s+(edit|modify|change|write)\b/i,
  /\b(read[- ]only|no file changes)\b/i,
  // 마/말 covers 하지 마, 하지 말고, 하지 말아줘 (precomposed Hangul: '말' is
  // not a match for the literal '마').
  /(수정|변경|편집)하지\s*(마|말)|(건드리지|바꾸지|고치지)\s*(마|말)/,
  /파일을?\s*(건드리지|바꾸지)/
];

const READ_ONLY_PATTERNS = [
  /\b(explain|describe|summarize|compare|analyze|inspect|find|show|what|why|how)\b/i,
  /(설명|요약|비교|분석|찾아|보여|무엇|왜|어떻게|알려)/
];

function matchesAny(prompt, patterns) {
  return patterns.some((pattern) => pattern.test(prompt));
}

export function classifyPrompt(prompt) {
  const normalized = typeof prompt === 'string' ? prompt.trim() : '';
  const highRisk = matchesAny(normalized, HIGH_RISK_PATTERNS);
  const destructive = matchesAny(normalized, DESTRUCTIVE_PATTERNS);
  const explicitReadOnly = matchesAny(normalized, EXPLICIT_READ_ONLY_PATTERNS);
  const development = !explicitReadOnly && matchesAny(normalized, DEVELOPMENT_PATTERNS);
  const readOnlyVerb = matchesAny(normalized, READ_ONLY_PATTERNS);
  const readOnly = explicitReadOnly || readOnlyVerb;

  if (explicitReadOnly) {
    // "Do not modify files" does not neutralize external actions: "push the
    // release tag to production, do not modify any files" still deploys.
    // Without an inspection verb, keep the gate closed at standard risk.
    if (highRisk && (!readOnlyVerb || destructive)) {
      return {
        requestClass: 'read-only',
        riskHint: 'standard',
        failPolicy: 'closed',
        requiresOrchestration: true
      };
    }
    return {
      requestClass: 'read-only',
      riskHint: 'low',
      failPolicy: 'open',
      requiresOrchestration: true
    };
  }
  if (highRisk && (development || !readOnly || destructive)) {
    return {
      requestClass: 'high-risk',
      riskHint: 'critical',
      failPolicy: 'closed',
      requiresOrchestration: true
    };
  }
  if (development) {
    return {
      requestClass: 'development',
      riskHint: 'standard',
      failPolicy: 'closed',
      requiresOrchestration: true
    };
  }
  return {
    requestClass: 'read-only',
    riskHint: 'low',
    failPolicy: 'open',
    requiresOrchestration: true,
    recognizedReadOnlyIntent: readOnly
  };
}

export function buildGateContext(classification) {
  return `aorch / adaptive-orchestrate: ${classification?.requestClass ?? 'unknown'}; risk hint ${classification?.riskHint ?? 'unknown'}. ` +
    'The lead decides decomposition outside this hook. Complete small clear work directly; delegate only for useful independence or context separation. ' +
    'Select provider, model, reasoning effort, skills, hooks and plugins only when needed. Preserve project permissions, change guard and relevant verification. ' +
    'Return delegated questions to this conversation. Synchronize definitions through install/update, never from a prompt hook.';
}
