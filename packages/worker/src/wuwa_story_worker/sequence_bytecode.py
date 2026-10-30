"""Bounded, pure resolution of straight-line CUE4Parse event bytecode.

Local and exactly receiver-scoped wrappers are expanded; unavailable receivers
remain opaque terminal calls. Names have no built-in meaning. Unsupported control
flow invalidates the result; dynamic operands remain symbolic dictionaries.
"""

from copy import deepcopy

MAX_STEPS = 2048
MAX_DEPTH = 32


class _Unsupported(Exception):
    pass


def _object_name(reference):
    name = reference.get('ObjectName', '') if isinstance(reference, dict) else reference
    if not isinstance(name, str):
        return ''
    return name.split("'", 1)[-1].removesuffix("'")


def _package(path):
    """Normalize export-directory prefixes, keeping the full game package path."""
    package, dot, suffix = path.rpartition('.')
    path = package if dot and suffix.isdigit() else path
    if 'Client/Content/' in path:
        return '/Game/' + path.split('Client/Content/', 1)[1]
    return path


def _class_key(reference):
    if not isinstance(reference, dict):
        return None
    name = reference.get('ObjectName', '')
    class_name = (_object_name(reference) if name.startswith('BlueprintGeneratedClass\'')
                  else name.split("'", 1)[0])
    return _package(reference.get('ObjectPath', '')), class_name


def resolve_event_calls(exports, function_ref):
    """Resolve one function reference without executing code or reading assets.

    References may be CUE ObjectName/ObjectPath dictionaries or unambiguous names.
    Calls contain ``function``, ``arguments``, ``receiver``, and ``source.trace``.
    Combined exports should retain ``Package`` and ``_export_index`` (the original
    package-local list index), or their owning class FuncMap. Trace entries use
    package-local export indices and also retain the combined input index.
    Unbound entry parameters are named symbols, not guessed runtime values.
    Diagnostics are dictionaries with code, message and source; fatal diagnostics
    discard partial calls. Input exports and their nested values are never changed.
    """
    functions = {i: e for i, e in enumerate(exports)
                 if isinstance(e, dict) and e.get('Type') == 'Function'}
    # FuncMap preserves package-local indices when multiple export lists are combined.
    function_paths = {}
    for export in exports:
        if isinstance(export, dict) and export.get('Type') == 'BlueprintGeneratedClass':
            key = (_package(export.get('Package', '')), export.get('Name'))
            for name, reference in export.get('FuncMap', {}).items():
                function_paths[key, name] = reference
    packages = {_package(e.get('Package') or e.get('Outer', {}).get('ObjectPath', ''))
                for e in functions.values()}

    def identity(index):
        export = functions[index]
        package = _package(export.get('Package') or export.get('Outer', {}).get('ObjectPath', ''))
        local_index = export.get('_export_index')
        if type(local_index) is not int:
            mapped = function_paths.get((_class_key(export.get('Outer')), export.get('Name')))
            suffix = mapped.get('ObjectPath', '').rsplit('.', 1)[-1] if mapped else ''
            local_index = int(suffix) if suffix.isdigit() else index if len(packages) == 1 else None
        return {'package': package, 'export_index': local_index, 'input_index': index}

    calls, diagnostics, persistent = [], [], {}
    steps = 0

    def diagnose(code, message, trace):
        diagnostics.append({'code': code, 'message': message,
                            'source': {'trace': list(trace)}})

    def fail(code, message, trace):
        diagnose(code, message, trace)
        raise _Unsupported

    def tick(trace, depth):
        nonlocal steps
        steps += 1
        if steps > MAX_STEPS or depth > MAX_DEPTH:
            fail('limit', 'Static resolution step/depth limit exceeded', trace)

    def resolve(reference, caller, trace):
        path = reference.get('ObjectPath') if isinstance(reference, dict) else None
        name = _object_name(reference)
        candidates = []
        for index, export in functions.items():
            outer = export.get('Outer', {})
            qualified = _object_name(outer) + ':' + export.get('Name', '')
            if path:
                target = identity(index)
                if (not isinstance(path, str) or not path.rsplit('.', 1)[-1].isdigit()
                        or _package(path) != target['package']
                        or path.rsplit('.', 1)[-1] != str(target['export_index'])):
                    continue
            if name and name not in (export.get('Name'), qualified):
                continue
            if caller is not None and _class_key(outer) != _class_key(functions[caller].get('Outer')):
                continue
            if path or name:
                candidates.append(index)
        if len(candidates) != 1:
            fail('function_reference', 'Missing or ambiguous local function reference', trace)
        return candidates[0]

    def field(reference, trace):
        if not isinstance(reference, dict):
            fail('property_reference', 'Missing field reference', trace)
        owner = resolve(reference.get('Owner'), None, trace)
        prop = reference.get('Property', {})
        name = prop.get('Name') if isinstance(prop, dict) else None
        if not name or not any(p.get('Name') == name
                               for p in functions[owner].get('ChildProperties', [])):
            fail('property_reference', 'Field is absent from its exact owner', trace)
        return owner, name

    def symbolic(node, trace, code='dynamic_operand'):
        diagnose(code, 'Operand cannot be statically resolved', trace)
        return {'symbol': 'expression', 'token': node.get('Token')}

    def value(node, locals_, trace, depth, self_):
        tick(trace, depth)
        if not isinstance(node, dict):
            return symbolic({}, trace)
        token = node.get('Token')
        if token == 'EX_Self':
            return self_
        if token in ('EX_StringConst', 'EX_UnicodeStringConst', 'EX_IntConst'):
            result = node.get('Value')
            expected = int if token == 'EX_IntConst' else str
            if type(result) is expected:
                return result
            return symbolic(node, trace)
        if token in ('EX_IntZero', 'EX_IntOne', 'EX_True', 'EX_False'):
            return {'EX_IntZero': 0, 'EX_IntOne': 1,
                    'EX_True': True, 'EX_False': False}[token]
        if token == 'EX_ObjectConst':
            reference = node.get('Value')
            if isinstance(reference, dict) and reference.get('ObjectPath'):
                return {'symbol': 'object', 'reference': deepcopy(reference)}
            return symbolic(node, trace)
        if token == 'EX_LocalVariable':
            key = field(node.get('Variable'), trace)
            if key in locals_:
                result = locals_[key]
            elif key in persistent:
                result = persistent[key]
            else:
                diagnose('unbound_field', 'Local/persistent field has no known value', trace)
                result = {'symbol': 'field', **identity(key[0]), 'property': key[1]}
            # Use the exact owner's declaration, never an inline field-name guess.
            declaration = next(p for p in functions[key[0]].get('ChildProperties', [])
                               if p.get('Name') == key[1])
            if (isinstance(result, dict) and result.get('symbol') in ('parameter', 'field')
                    and not result.get('class') and declaration.get('PropertyClass')):
                return dict(result, **{'class': deepcopy(declaration['PropertyClass'])})
            return result
        return symbolic(node, trace)

    def invoke(index, arguments, trace, active):
        tick(trace, len(active))
        if index in active:
            fail('cycle', 'Recursive local function call', trace)
        active = (*active, index)
        export = functions[index]
        self_ = {'symbol': 'self', 'class': deepcopy(export.get('Outer'))}
        parameters = [p for p in export.get('ChildProperties', [])
                      if 'Parm' in p.get('PropertyFlags', '').split(' | ')
                      and 'ReturnParm' not in p.get('PropertyFlags', '').split(' | ')]
        if arguments is not None and len(arguments) != len(parameters):
            fail('arguments', 'Local call argument count differs from Parm fields', trace)
        locals_ = {}
        for position, parameter in enumerate(parameters):
            locals_[index, parameter['Name']] = (
                arguments[position] if arguments is not None else
                {'symbol': 'parameter', **identity(index), 'property': parameter['Name'],
                 'class': deepcopy(parameter.get('PropertyClass'))})
        code = export.get('ScriptBytecode')
        if not isinstance(code, list) or not code:
            fail('bytecode', 'Function has no decoded ScriptBytecode', trace)
        addresses = {}
        for position, node in enumerate(code):
            address = node.get('StatementIndex') if isinstance(node, dict) else None
            if type(address) is not int or address < 0 or address in addresses:
                fail('statement_index', 'Missing or duplicate statement byte offset', trace)
            addresses[address] = position
        position, visited = 0, set()
        while position < len(code):
            node = code[position]
            location = {**identity(index), 'owner': deepcopy(export.get('Outer')),
                        'function': export.get('Name'),
                        'statement_index': node['StatementIndex'], 'token': node.get('Token')}
            trace = [*trace, location]
            tick(trace, len(active))
            if position in visited:
                fail('cycle', 'Repeated statement byte offset', trace)
            visited.add(position)
            token = node.get('Token')
            if token in ('EX_EndOfScript', 'EX_Return'):
                if token == 'EX_Return' and node.get('Expression', {}).get('Token') != 'EX_Nothing':
                    fail('return_value', 'Only void event returns are supported', trace)
                return
            if token in ('EX_Jump', 'EX_ComputedJump'):
                target = (node.get('CodeOffset') if token == 'EX_Jump' else
                          value(node.get('CodeOffsetExpression'), locals_, trace, len(active), self_))
                if type(target) is not int or target not in addresses:
                    fail('jump_target', 'Jump has no exact static statement target', trace)
                position = addresses[target]
                continue
            if token == 'EX_LetValueOnPersistentFrame':
                key = field(node.get('DestinationProperty'), trace)
                if _class_key(functions[key[0]].get('Outer')) != _class_key(export.get('Outer')):
                    fail('property_reference', 'Persistent write crosses local owner', trace)
                persistent[key] = value(node.get('AssignmentExpression'), locals_, trace, len(active), self_)
            elif token in ('EX_LocalVirtualFunction', 'EX_LocalFinalFunction'):
                target = resolve(node.get('Function'), index, trace)
                operands = node.get('Parameters')
                if not isinstance(operands, list):
                    fail('arguments', 'Missing call parameters', trace)
                arguments = [value(p, locals_, trace, len(active), self_) for p in operands]
                invoke(target, arguments, trace, active)
            elif token == 'EX_Context':
                receiver = value(node.get('ObjectExpression'), locals_, trace, len(active), self_)
                call = node.get('ContextExpression', {})
                if (call.get('Token') != 'EX_LocalVirtualFunction'
                        or not isinstance(call.get('Function'), str)
                        or not isinstance(call.get('Parameters'), list)):
                    fail('context', 'Unsupported external context expression', trace)
                arguments = [value(p, locals_, trace, len(active), self_) for p in call['Parameters']]
                reference = (receiver.get('class') or receiver.get('reference')
                             if isinstance(receiver, dict) else None)
                targets = [i for i, e in functions.items()
                           if reference and _class_key(e.get('Outer')) == _class_key(reference)
                           and e.get('Name') == call['Function']]
                if len(targets) > 1:
                    fail('function_reference', 'Ambiguous receiver-scoped method', trace)
                if targets:
                    invoke(targets[0], arguments, trace, active)
                else:
                    calls.append({'function': call['Function'], 'arguments': arguments,
                                  'receiver': receiver, 'source': {'trace': list(trace)}})
            else:
                fail('control_flow', f'Unsupported statement: {token}', trace)
            position += 1
        fail('control_flow', 'Function falls through without a terminator', trace)

    try:
        invoke(resolve(function_ref, None, []), None, [], ())
    except _Unsupported:
        calls.clear()
    return {'calls': calls, 'diagnostics': diagnostics}
