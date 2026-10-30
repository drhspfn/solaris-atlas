"""Self-authored bytecode fixtures; no client assets are embedded or required."""

from copy import deepcopy

import pytest

from wuwa_story_worker import sequence_bytecode
from wuwa_story_worker.sequence_bytecode import resolve_event_calls


def const(token, value=None):
    return {'Token': 'EX_' + token, 'Value': value}


def reference(package, owner, name, index):
    return {'ObjectName': f"Function'{owner}:{name}'", 'ObjectPath': f'{package}.{index}'}


def prop(owner, name):
    return {'Owner': owner, 'Property': {'Name': name}}


def variable(owner, name):
    return {'Token': 'EX_LocalVariable', 'Variable': prop(owner, name)}


def call(name, *parameters):
    return {'Token': 'EX_LocalVirtualFunction', 'Function': name,
            'Parameters': list(parameters)}


def external(name, *parameters, receiver=None):
    return {'Token': 'EX_Context',
            'ObjectExpression': receiver or const('ObjectConst', {
                'ObjectName': "Library_C'Default__Library_C'",
                'ObjectPath': '/Game/Library'}),
            'ContextExpression': call(name, *parameters)}


def function(name, *statements, parameters=(), fields=()):
    code = [dict(node, StatementIndex=i * 10) for i, node in enumerate(statements)]
    code += [{'Token': 'EX_Return', 'StatementIndex': len(code) * 10,
              'Expression': {'Token': 'EX_Nothing'}}]
    return {'Type': 'Function', 'Name': name,
            'ChildProperties': [{'Name': p, 'PropertyFlags': 'BlueprintVisible | Parm',
                                 **({'PropertyClass': c} if c else {})}
                                for p, c in parameters] + [{'Name': p} for p in fields],
            'ScriptBytecode': code}


def package(path, owner, *functions):
    outer = {'ObjectName': f"BlueprintGeneratedClass'{owner}'",
             'ObjectPath': f'{path}.{len(functions)}'}
    return [dict(f, Outer=outer) for f in functions] + [{
        'Type': 'BlueprintGeneratedClass', 'Name': owner, 'Package': path,
        'FuncMap': {f['Name']: reference(path, owner, f['Name'], i)
                    for i, f in enumerate(functions)}}]


def wrapped_exports():
    graph = reference('/Game/Scene', 'Director_C', 'Graph', 0)
    wrapper = reference('/Game/Scene', 'Director_C', 'Wrapper', 1)
    entry = reference('/Game/Scene', 'Director_C', 'Entry', 2)
    manager_class = {'ObjectName': "BlueprintGeneratedClass'Manager_C'",
                     'ObjectPath': '/Game/Manager'}
    graph_function = function('Graph',
        {'Token': 'EX_ComputedJump', 'CodeOffsetExpression': variable(graph, 'Offset')},
        external('Unreachable'),
        external('展示_Ω', variable(graph, 'SavedText'), const('False'),
                 receiver=variable(graph, 'SavedObject')),
        {'Token': 'EX_Jump', 'CodeOffset': 40},
        parameters=(('Offset', None),), fields=('SavedText', 'SavedObject'))
    wrapper_function = function('Wrapper',
        {'Token': 'EX_LetValueOnPersistentFrame',
         'DestinationProperty': prop(graph, 'SavedObject'),
         'AssignmentExpression': variable(wrapper, 'Object')},
        {'Token': 'EX_LetValueOnPersistentFrame',
         'DestinationProperty': prop(graph, 'SavedText'),
         'AssignmentExpression': variable(wrapper, 'Text')},
        {'Token': 'EX_LocalFinalFunction', 'Function': graph,
         'Parameters': [const('IntConst', 20)]},
        parameters=(('Object', manager_class), ('Text', None)))
    entry_function = function('Entry', call('Wrapper', variable(entry, 'Object'),
                                           const('UnicodeStringConst', 'fixture clip')),
                              parameters=(('Object', manager_class),))
    sequence = package('local/Client/Content/Scene', 'Director_C',
                       graph_function, wrapper_function, entry_function)
    manager_ref = reference('/Game/Manager', 'Manager_C', '展示_Ω', 0)
    manager = package('other/Client/Content/Manager', 'Manager_C', function('展示_Ω',
        external('Terminal', variable(manager_ref, 'Text'), variable(manager_ref, 'Loop'),
                 const('Self')), parameters=(('Text', None), ('Loop', None))))
    return sequence, manager, entry


def test_wrappers_persistent_fields_offsets_and_cross_package_context_are_pure():
    sequence, manager, entry = wrapped_exports()
    exports = sequence + manager
    original = deepcopy(exports)
    result = resolve_event_calls(exports, entry)
    assert result['diagnostics'] == []
    assert len(result['calls']) == 1
    terminal = result['calls'][0]
    assert terminal['function'] == 'Terminal'
    assert terminal['arguments'][:2] == ['fixture clip', False]
    assert terminal['arguments'][2]['symbol'] == 'self'
    assert terminal['receiver']['reference']['ObjectPath'] == '/Game/Library'
    trace = terminal['source']['trace']
    assert [p['function'] for p in trace] == [
        'Entry', 'Wrapper', 'Wrapper', 'Wrapper', 'Graph', 'Graph', '展示_Ω']
    assert trace[-2]['statement_index'] == 20
    assert exports == original
    # Results cannot mutate the input, and persistent bindings never leak between runs.
    terminal['receiver']['reference']['ObjectPath'] = 'changed'
    assert exports == original
    assert resolve_event_calls(exports, entry)['calls'][0]['receiver'] != terminal['receiver']
    assert resolve_event_calls(sequence, 'Graph')['calls'] == []


def test_unavailable_external_class_stays_opaque_with_original_method_name():
    sequence, _, entry = wrapped_exports()
    result = resolve_event_calls(sequence, entry)
    assert result['diagnostics'] == []
    assert result['calls'][0]['function'] == '展示_Ω'
    assert result['calls'][0]['arguments'] == ['fixture clip', False]
    assert result['calls'][0]['receiver']['symbol'] == 'parameter'


@pytest.mark.parametrize(('token', 'raw', 'expected'), [
    ('StringConst', 'text', 'text'), ('UnicodeStringConst', '非英文', '非英文'),
    ('IntConst', -7, -7), ('IntZero', None, 0), ('IntOne', None, 1),
    ('True', None, True), ('False', None, False)])
def test_literal_operands(token, raw, expected):
    exports = package('/Game/Constants', 'Owner_C',
                      function('Entry', external('Terminal', const(token, raw))))
    result = resolve_event_calls(exports, 'Entry')
    assert result['diagnostics'] == []
    assert result['calls'][0]['arguments'] == [expected]
    assert type(result['calls'][0]['arguments'][0]) is type(expected)


def test_unknown_operand_is_symbolic_and_diagnosed_without_guessing():
    exports = package('/Game/Constants', 'Owner_C', function('Entry',
        external('Terminal', {'Token': 'EX_InstanceVariable', 'Variable': 'runtime'})))
    result = resolve_event_calls(exports, 'Entry')
    assert result['calls'][0]['arguments'] == [
        {'symbol': 'expression', 'token': 'EX_InstanceVariable'}]
    assert result['diagnostics'][0]['code'] == 'dynamic_operand'


@pytest.mark.parametrize('statement', [
    {'Token': 'EX_JumpIfNot', 'CodeOffset': 20, 'BooleanExpression': const('True')},
    {'Token': 'EX_PushExecutionFlow', 'CodeOffset': 20},
    {'Token': 'EX_UnknownControl'},
    {'Token': 'EX_Jump', 'CodeOffset': 999},
    {'Token': 'EX_Jump', 'CodeOffset': True},
    {'Token': 'EX_ComputedJump', 'CodeOffsetExpression': const('StringConst', '20')},
    {'Token': 'EX_Jump', 'CodeOffset': 0},
    call('Entry')])
def test_unsupported_control_or_cycles_discard_partial_calls(statement):
    exports = package('/Game/Control', 'Owner_C',
                      function('Entry', external('BeforeFailure'), statement))
    result = resolve_event_calls(exports, 'Entry')
    assert result['calls'] == []
    assert result['diagnostics']


def test_exact_owner_and_package_refs_prevent_coincidental_name_resolution():
    sequence, manager, entry = wrapped_exports()
    decoy = package('/Game/Decoy', 'Manager_C', function('展示_Ω',
                    external('Wrong'), parameters=(('a', None), ('b', None))))
    combined = decoy + manager + sequence  # Export list offsets are deliberately different.
    assert resolve_event_calls(combined, entry)['calls'][0]['function'] == 'Terminal'
    assert resolve_event_calls(combined, '展示_Ω')['calls'] == []
    bad = dict(entry, ObjectName="Function'WrongOwner_C:Entry'")
    assert resolve_event_calls(combined, bad)['calls'] == []
    bad = dict(entry, ObjectPath='/Game/Absent.2')
    assert resolve_event_calls(combined, bad)['calls'] == []
    bad = dict(entry, ObjectPath='/Game/Scene.0')
    assert resolve_event_calls(combined, bad)['calls'] == []


def test_combined_exports_use_explicit_original_indices_without_class_funcmaps():
    sequence, manager, entry = wrapped_exports()
    for exports, path in ((sequence, '/Game/Scene'), (manager, '/Game/Manager')):
        for index, export in enumerate(exports):
            export['Package'] = path
            export['_export_index'] = index
    exports = [e for e in manager + sequence if e['Type'] == 'Function']
    result = resolve_event_calls(exports, entry)
    assert result['diagnostics'] == []
    assert result['calls'][0]['function'] == 'Terminal'
    trace = result['calls'][0]['source']['trace']
    assert (trace[0]['package'], trace[0]['export_index']) == ('/Game/Scene', 2)
    assert (trace[-1]['package'], trace[-1]['export_index']) == ('/Game/Manager', 0)
    assert trace[-1]['input_index'] != trace[-1]['export_index'] or trace[0]['input_index'] != 2


def test_context_object_constant_resolves_only_its_exact_class_package():
    receiver = const('ObjectConst', {'ObjectName': "Manager_C'Default__Manager_C'",
                                    'ObjectPath': '/Game/Manager'})
    exports = package('/Game/Caller', 'Caller_C',
                      function('Entry', external('Forward', const('IntOne'), receiver=receiver)))
    method = reference('/Game/Manager', 'Manager_C', 'Forward', 0)
    exports += package('/Game/Manager', 'Manager_C', function('Forward',
        external('Terminal', variable(method, 'value')), parameters=(('value', None),)))
    exports += package('/Game/OtherManager', 'Manager_C', function('Forward',
        external('Wrong'), parameters=(('value', None),)))
    result = resolve_event_calls(exports, 'Entry')
    assert result['diagnostics'] == []
    assert result['calls'][0]['function'] == 'Terminal'
    assert result['calls'][0]['arguments'] == [1]


def test_context_uses_exact_persistent_field_class_when_entry_type_is_unknown():
    sequence, manager, entry = wrapped_exports()
    manager_class = sequence[2]['ChildProperties'][0].pop('PropertyClass')
    sequence[1]['ChildProperties'][0].pop('PropertyClass')
    declaration = next(p for p in sequence[0]['ChildProperties'] if p['Name'] == 'SavedObject')
    declaration['PropertyClass'] = manager_class
    # An inconsistent inline property type cannot redirect the declared field owner.
    sequence[0]['ScriptBytecode'][2]['ObjectExpression']['Variable']['Property'][
        'PropertyClass'] = {'ObjectName': "BlueprintGeneratedClass'Decoy_C'",
                            'ObjectPath': '/Game/Decoy'}
    result = resolve_event_calls(sequence + manager, entry)
    assert result['diagnostics'] == []
    assert result['calls'][0]['function'] == 'Terminal'


def test_identical_local_names_in_different_functions_do_not_share_values():
    one = reference('/Game/Owner', 'Owner_C', 'One', 1)
    two = reference('/Game/Owner', 'Owner_C', 'Two', 2)
    exports = package('/Game/Owner', 'Owner_C',
        function('Entry', call('One', const('StringConst', 'bound')), call('Two')),
        function('One', external('First', variable(one, 'same')), parameters=(('same', None),)),
        function('Two', external('Second', variable(two, 'same')), fields=('same',)))
    result = resolve_event_calls(exports, 'Entry')
    assert result['calls'][0]['arguments'] == ['bound']
    assert result['calls'][1]['arguments'][0]['symbol'] == 'field'
    assert result['diagnostics'][0]['code'] == 'unbound_field'


def test_local_virtual_call_does_not_search_other_classes():
    exports = package('/Game/One', 'One_C', function('Entry', call('Missing')))
    exports += package('/Game/Two', 'Two_C', function('Missing', external('Wrong')))
    result = resolve_event_calls(exports, 'Entry')
    assert result['calls'] == []
    assert result['diagnostics'][0]['code'] == 'function_reference'


def test_field_owner_is_validated_instead_of_using_its_name():
    sequence, manager, entry = wrapped_exports()
    sequence[1]['ScriptBytecode'][1]['AssignmentExpression']['Variable']['Owner'] = {
        'ObjectName': "Function'Other_C:Wrapper'", 'ObjectPath': '/Game/Scene.1'}
    result = resolve_event_calls(sequence + manager, entry)
    assert result['calls'] == []
    assert result['diagnostics'][0]['code'] == 'function_reference'


@pytest.mark.parametrize('limit', ['MAX_STEPS', 'MAX_DEPTH'])
def test_resolution_has_explicit_global_step_and_recursion_limits(monkeypatch, limit):
    sequence, manager, entry = wrapped_exports()
    monkeypatch.setattr(sequence_bytecode, limit, 2)
    result = resolve_event_calls(sequence + manager, entry)
    assert result['calls'] == []
    assert result['diagnostics'][-1]['code'] == 'limit'


def test_argument_arity_and_duplicate_statement_addresses_are_rejected():
    exports = package('/Game/Bad', 'Owner_C', function('Entry', call('NeedsArg')),
                      function('NeedsArg', external('Wrong'), parameters=(('value', None),)))
    assert resolve_event_calls(exports, 'Entry')['diagnostics'][0]['code'] == 'arguments'
    exports[0]['ScriptBytecode'][1]['StatementIndex'] = 0
    assert resolve_event_calls(exports, 'Entry')['diagnostics'][0]['code'] == 'statement_index'
