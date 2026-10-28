"""Shared, answer-independent facts and validation for width agents.

These guards validate model outputs; they never manufacture a tool plan.
"""
import ast
import copy
import math
import re


WIDTH_RULES = """
Width rules shared by extraction, checking and planning:
- lane_position_table is authoritative. left/right ordinals use the COMPLETE
  supplied left-to-right list, not abs(lane_id), screen position, or sign filtering.
  Only explicit left/right carriageway requests filter by positive/negative IDs.
- lane_width_rank lists all tied narrowest/widest lanes. One lane can be both.
- Increase/decrease BY a length uses relative + increase/decrease + numeric + m.
  SET TO a final length uses absolute + set + numeric + m. Relative is not limited
  to percentages. A directional request without a degree uses qualitative/default.
- Explicit "a bit", "a little", "slightly" mean qualitative/slight; "greatly",
  "significantly" mean qualitative/significant. Never replace an explicit degree
  with default. Signed lane IDs (including negative IDs) are legitimate IDs.
- "moderately" means default; "noticeably" means significant. A request containing
  only the direction narrow/widen and no amount or degree means qualitative/default.
- Do not reject a request because its projected resulting width is negative, or
  cap a requested reduction to the current width. Emit the original requested
  magnitude and sign through DSL/tools; actual map width limits belong to the
  scene executor. There is no DSL maximum percentage reduction.
- Example: lane_ids=[-1,-2,-3] has leftmost=-1, rightmost=-3, first from right=-3.
  A negative-only road is not ambiguous. Never substitute -1 for rightmost.
- Do not invent minimum-width, safety, dynamic-target, or ambiguity constraints.
  Use only the project's stated request-conflict rules and the supplied snapshot.
- If road_data includes original_user_instruction, use it to verify the meaning
  of the current subtask. A Manager paraphrase cannot change the requested edit.
- Retries must read the original request. Change only fields supported by a
  concrete issue; revalidate all other fields without gratuitously changing them.

FINAL WIDTH ATTENTION CHECK (internal only; do not add output fields):
1. Read the target road's actual lane_ids, not a remembered OpenDRIVE convention.
2. For rightmost/far right choose the LAST entry of that COMPLETE ordered list;
   for leftmost/far left choose its FIRST entry. The sign or absolute lane ID is
   not a position. With [5,2,-1,-4], rightmost=-4; with [-2,-7,-9], rightmost=-9.
3. For an ordinal read from the requested end of the complete list; filter by
   sign only if the user explicitly asks for a half-road/carriageway.
4. Compare this choice with lane_position_table before writing lane_ids. Preserve
   the same chosen ID in every corresponding tool-plan item. Do not switch to -1
   merely because the instruction says 'right'.
5. Re-read the ORIGINAL degree phrase: a bit/a little/slightly=>slight,
   moderately=>default, noticeably/greatly/significantly=>significant. No degree
   or amount, but an explicit widening/narrowing direction=>qualitative/default.
These are width-only rules; do not change the other operation schemas.
"""

CHECK_RULES = """
Return exactly passed, problem_source, request_status, dsl_status, issues,
check_result. request_status: valid/invalid/insufficient_context; dsl_status:
correct/incorrect/undetermined. issues is an array of objects containing exactly
source(dsl/request/context), code, field, expected, actual, reason (nonempty strings).
For a DSL issue identify a concrete differing field, expected value, actual value,
and evidence in the ORIGINAL request or authoritative context. A speculative risk
is not an issue. Do not flag a field when expected and actual are equal.
No issues + valid request + correct DSL => passed=true, problem_source=none,
check_result=OK. Otherwise passed=false and derive problem_source from issue
sources: dsl=>extraction_error, request=>user_requirement_error,
context=>context_error, mixed=>both. Never contradict these structured fields.
An invalid checker response is not evidence that the DSL is wrong. On checker
retry, inspect the SAME DSL; use reviewHistory to avoid reversing established
facts without concrete new evidence.

Before returning, perform these final evidence checks internally:
1. Read each issues.actual from the CURRENT dsl_output, not review_history or
   your previous explanation. For a path ending magnitude.kind, if current kind
   is qualitative, you cannot claim actual=numeric. Remove stale issues.
2. If lane_width_rank.narrowest/widest contains ONE ID, that target is uniquely
   determined. Do not claim a tie, dynamic ambiguity, or that two lanes are needed.
3. For leftmost/rightmost or ordinal targets use lane_position_table exactly,
   including when all IDs are negative. Do not add a travel-direction requirement.
4. Direction-only narrowing/widening => qualitative/default, not an invented
   numeric amount. moderately=>default, a bit/a little=>slight. Reject neither
   correct default nor correct qualitative simply because direction is explicit.
5. If expected equals actual, or your reason concludes the alleged issue is
   incorrect, DELETE that issue and recompute all six output fields. Do not leave
   your deliberation, reversal, or abandoned hypothesis in the final JSON.
6. Projected negative width is not a request error or a DSL error. Scene bounds
   are outside this checker. Do not claim you validated the resulting map.

Examples of correct checks (all must return passed=true and issues=[]):
- lane_ids=[-1,-2,-3], narrowest=[-2], request='noticeably widen the narrowest
  lane', DSL targets lane_-2, qualitative/significant.
- same lane list, request='moderately narrow the far left lane', DSL targets
  lane_-1, qualitative/default. Negative IDs do not change physical order.
- request='narrowing lane -1', CURRENT DSL uses qualitative/default/decrease:
  pass even if a prior retry used numeric/0.5; the prior retry is not current DSL.
- request='first lane on the right', same list, DSL lane_-3: pass, never ask
  the extractor to replace it with lane_-1.
"""


def width_context(info):
    info = copy.deepcopy(info or {})
    ids = list(dict.fromkeys(int(x) for x in info.get('lane_ids', [])))
    info['lane_ids'] = ids  # preserve the project's supplied physical order
    info['lane_position_table'] = [
        {'lane_id': x, 'from_left': i + 1, 'from_right': len(ids) - i}
        for i, x in enumerate(ids)
    ]
    widths = info.get('lane_avg_width', {})
    available = {x: float(widths[str(x)]) for x in ids if str(x) in widths}
    if available:
        info['lane_width_rank'] = {
            'narrowest': [x for x, w in available.items() if w == min(available.values())],
            'widest': [x for x, w in available.items() if w == max(available.values())],
        }
    return info


def road_ids(text):
    """Recognize explicit ROAD tokens, never the 'road' suffix of a lane phrase."""
    patterns = (r'\broad(?:way)?(?:\s*no\.?)?[_\s#-]*(\d+)\b',
                r'(\d+)\s*号\s*(?:道路|公路|路)(?!\s*\d)',
                r'(?:道路|公路)\s*(\d+)(?!\d|\s*号?\s*车道)')
    return list(dict.fromkeys(x for p in patterns for x in re.findall(p, str(text), re.I)))


def width_combination_errors(operation):
    change = operation.get('change', {})
    if not isinstance(change, dict):
        return []
    mag = change.get('magnitude', {})
    if not isinstance(mag, dict):
        return []  # basic schema validation reports these
    mode, action, kind, unit = (change.get('mode'), change.get('action'),
                                mag.get('kind'), mag.get('unit'))
    if action == 'none':
        return []
    allowed = {('absolute', 'set', 'numeric', 'm'), ('relative', 'set', 'numeric', 'percent')}
    allowed.update(('relative', a, k, u) for a in ('increase', 'decrease')
                   for k, u in (('numeric', 'm'), ('numeric', 'percent'), ('qualitative', 'none')))
    errors = []
    if (mode, action, kind, unit) not in allowed:
        errors.append('invalid width mode/action/kind/unit combination')
    lanes = operation.get('lane_ids')
    if isinstance(lanes, list) and (not lanes or len({repr(x) for x in lanes}) != len(lanes)):
        errors.append('width lane_ids must be nonempty and unique; use none for whole-road scope')
    value = mag.get('value')
    if kind == 'numeric' and isinstance(value, (float, int)) and not isinstance(value, bool):
        if not math.isfinite(value) or (action != 'set' and value <= 0):
            errors.append('width numeric magnitude must be finite and positive for increase/decrease')
    return errors


def checker_errors(result):
    if not isinstance(result, dict):
        return ['checker must return an object']
    fields = {'passed', 'problem_source', 'request_status', 'dsl_status', 'issues', 'check_result'}
    errors = []
    if set(result) != fields:
        errors.append('checker must return exactly the six defined fields')
    passed, source = result.get('passed'), result.get('problem_source')
    request, dsl, issues = result.get('request_status'), result.get('dsl_status'), result.get('issues')
    if type(passed) is not bool:
        errors.append('passed must be a boolean')
    if request not in ('valid', 'invalid', 'insufficient_context'):
        errors.append('invalid request_status')
    if dsl not in ('correct', 'incorrect', 'undetermined'):
        errors.append('invalid dsl_status')
    if not isinstance(result.get('check_result'), str) or not result['check_result'].strip():
        errors.append('check_result must be nonempty')
    if not isinstance(issues, list):
        return errors + ['issues must be an array']
    sources = set()
    for issue in issues:
        if not isinstance(issue, dict) or set(issue) != {'source', 'code', 'field', 'expected', 'actual', 'reason'}:
            errors.append('invalid issue structure')
            continue
        if any(not isinstance(v, str) or not v.strip() for v in issue.values()):
            errors.append('issue fields must be nonempty strings')
        if issue['source'] not in ('dsl', 'request', 'context'):
            errors.append('invalid issue source')
        sources.add(issue['source'])
        if issue['source'] == 'dsl' and issue['expected'] == issue['actual']:
            errors.append('DSL issue expected and actual are identical')
        if issue['source'] == 'dsl' and not re.fullmatch(r'operations(?:\[\d+\])?(?:\.[a-z_]+)*', str(issue['field'])):
            errors.append('DSL issue field must be a concrete operations path')
    inferred = 'none' if not sources else ('both' if len(sources) > 1 else
        {'dsl': 'extraction_error', 'request': 'user_requirement_error', 'context': 'context_error'}.get(next(iter(sources))))
    if source != inferred:
        errors.append('problem_source disagrees with issues')
    if request == 'valid' and sources & {'request', 'context'}:
        errors.append('valid request cannot have request/context issues')
    if request == 'invalid' and 'request' not in sources:
        errors.append('invalid request needs request evidence')
    if request == 'insufficient_context' and 'context' not in sources:
        errors.append('insufficient_context needs context evidence')
    if (dsl == 'incorrect') != ('dsl' in sources):
        errors.append('dsl_status disagrees with issues')
    if dsl == 'undetermined' and 'context' not in sources:
        errors.append('undetermined DSL requires context evidence')
    expected = request == 'valid' and dsl == 'correct' and not issues
    if passed is not expected or (result.get('check_result') == 'OK') != expected:
        errors.append('passed/check_result disagree with structured status')
    return errors


def unrelated_repair_changes(previous, current, issues):
    """Reject collateral edits; never merge/patch a model-generated DSL locally."""
    allowed = [i['field'] for i in issues if i.get('source') == 'dsl']
    changed = []

    def visit(a, b, path):
        if a == b:
            return
        if any(path == p or path.startswith(p + '.') or path.startswith(p + '[') for p in allowed):
            return
        if isinstance(a, dict) and isinstance(b, dict) and set(a) == set(b):
            for key in a:
                visit(a[key], b[key], f'{path}.{key}' if path else key)
        elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
            for n, (x, y) in enumerate(zip(a, b)):
                visit(x, y, f'{path}[{n}]')
        else:
            changed.append(path)

    visit(previous, current, '')
    return changed


def _interval(node):
    if isinstance(node, ast.Name):
        raise ValueError('Calculation formulas cannot contain variables')
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        value = float(node.value)
        if not math.isfinite(value):
            raise ValueError('formula must be finite')
        return value, value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        lo, hi = _interval(node.operand)
        return (-hi, -lo) if isinstance(node.op, ast.USub) else (lo, hi)
    if isinstance(node, ast.BinOp):
        a, b = _interval(node.left)
        c, d = _interval(node.right)
        if isinstance(node.op, ast.Add):
            return a+c, b+d
        if isinstance(node.op, ast.Sub):
            return a-d, b-c
        if isinstance(node.op, ast.Mult):
            values = [a*c, a*d, b*c, b*d]
            return min(values), max(values)
        if isinstance(node.op, ast.Div) and not c <= 0 <= d:
            values = [a/c, a/d, b/c, b/d]
            return min(values), max(values)
        if isinstance(node.op, ast.Pow) and c == d and abs(c) <= 10:
            if (a < 0 and not c.is_integer()) or (a <= 0 <= b and c < 0):
                raise ValueError('formula power is not real and bounded')
            values = [a**c, b**c]
            if a <= 0 <= b and c > 0:
                values.append(0)
            return min(values), max(values)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
        args = [_interval(x) for x in node.args]
        if node.func.id == 'random' and len(args) == 2 and all(a == b for a, b in args):
            lo, hi = args[0][0], args[1][0]
            if lo <= hi:
                return lo, hi
        if node.func.id in ('min', 'max') and args:
            fn = min if node.func.id == 'min' else max
            return fn(a for a, _ in args), fn(b for _, b in args)
        if node.func.id == 'abs' and len(args) == 1:
            a, b = args[0]
            return (0 if a <= 0 <= b else min(abs(a), abs(b))), max(abs(a), abs(b))
    raise ValueError('formula cannot be verified with supported safe interval arithmetic')


def validate_formula(item, operation, info):
    """Validate formula range without sampling random or replacing the model formula."""
    formula = item['formula']
    tree = ast.parse(formula, mode='eval')
    if len(list(ast.walk(tree))) > 150:
        raise ValueError('formula too complex')
    actual = _interval(tree.body)
    widths = info.get('lane_avg_width', {})
    ids = item['li'] or info.get('lane_ids', [])
    change, mag = operation['change'], operation['change']['magnitude']
    needs_base = change['action'] == 'set' or mag['kind'] == 'qualitative' or mag['unit'] == 'percent'
    if not ids or (needs_base and any(str(x) not in widths for x in ids)):
        raise ValueError('formula validation needs every target lane width')
    base = sum(float(widths[str(x)]) for x in ids) if needs_base else 0
    divisor = 1 if item['li'] else len(ids)
    sign = -1 if change['action'] == 'decrease' else 1
    if mag['kind'] == 'qualitative':
        lo, hi = {'slight': (0.05, 0.25), 'default': (0.25, 0.40), 'significant': (0.40, 0.70)}[mag['level']]
        expected = sorted([sign*base*lo/divisor, sign*base*hi/divisor])
    else:
        if any(isinstance(x, ast.Call) and isinstance(x.func, ast.Name) and x.func.id == 'random' for x in ast.walk(tree)):
            raise ValueError('numeric request cannot use a random formula')
        amount = float(mag['value'])
        if change['action'] == 'set':
            delta = (amount if mag['unit'] == 'm' else base*amount/100) - base
        else:
            delta = sign*(amount if mag['unit'] == 'm' else base*amount/100)
        expected = [delta/divisor]*2
    tolerance = 0.00051
    if not all(math.isfinite(x) for x in actual) or any(abs(x-y) > tolerance for x, y in zip(actual, expected)):
        raise ValueError(f'Width formula result range {actual} disagrees with DSL/context range {tuple(expected)}')
