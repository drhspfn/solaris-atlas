"""Decode pinned client NPC configuration into compiler inputs, retaining byte evidence."""

import hashlib
import json
from contextlib import closing
from pathlib import Path

from flatbuffers.number_types import Int32Flags
from flatbuffers.table import Table

from wuwa_story_worker.map_sources import flag, integer, open_db, strings, table, text


def _vector(row, field, pairs=False):
    offset = row.Offset(4 + field * 2)
    values = []
    if offset:
        for index in range(row.VectorLen(offset)):
            at = row.Vector(offset) + index * 4
            if pairs:
                item = Table(row.Bytes, row.Indirect(at))
                values.append({'Key': integer(item, 0), 'Value': integer(item, 1)})
            else:
                values.append(row.Get(Int32Flags, at))
    return values


def decode_shop(blob, fixed=False):
    row = table(blob)
    if not fixed:
        return {'Id': integer(row, 0), 'ShopType': integer(row, 1), 'ShopName': text(row, 2),
                'OpenId': integer(row, 3), 'ShowCurrency': _vector(row, 4),
                'DialogueText': text(row, 5), 'UiCamera': text(row, 6)}
    fields = ('Id', 'ShopId', 'SortIndex', 'ItemId', 'ItemNum', 'CondId',
              'VisableCondition', 'DisableCondition', 'LimitNum')
    result = {name: integer(row, index, {4: 1, 8: -1}.get(index, 0))
              for index, name in enumerate(fields)}
    result.update(OriginalPrice=_vector(row, 9, True), Price=_vector(row, 10, True),
                  Show=flag(row, 11) if row.Offset(26) else True)
    result.update({name: text(row, index) for index, name in enumerate(
        ('Label', 'SwitchText', 'PurchaseText'), 12)})
    return result


TABLES = (
    ('db_blueprint.db', 'blueprintconfig', 'BinData/blueprint/blueprintconfig.json'),
    ('db_template.db', 'templateconfig', 'BinData/template/templateconfig.json'),
    ('db_level_entity.db', 'levelentityconfig', 'BinData/level_entity/levelentityconfig.json'),
    ('db_AbpMontageData.db', 'abpmontagedata', 'BinData/AbpMontageData/abpmontagedata.json'),
    ('db_MontageData.db', 'montagedata', 'BinData/MontageData/montagedata.json'),
    ('db_speaker.db', 'speaker', 'BinData/speaker/speaker.json'),
    ('db_shop.db', 'shopinfo', 'BinData/shop/shopinfo.json'),
    ('db_shop.db', 'shopfixed', 'BinData/shop/shopfixed.json'),
    ('db_model_config_preload.db', 'modelconfigpreload', 'BinData/model_config_preload/modelconfigpreload.json'),
)


def native_rows(config, database, name, asset_version):
    """Tables/SQL are constants; a missing or mismatched source fails the import."""
    if (database, name) not in {(db, tbl) for db, tbl, _ in TABLES}:
        raise ValueError('Unsupported NPC source table')
    with closing(open_db(config / database)) as connection:
        for identity, blob in connection.execute(f'SELECT Id, BinData FROM {name} ORDER BY Id'):
            row = table(blob)
            if integer(row, 0) != identity:
                raise ValueError(f'NPC source row ID mismatch: {database}/{name}/{identity}')
            record = {'Id': identity}
            if name == 'blueprintconfig':
                record.update(BlueprintType=text(row, 1), EntityType=text(row, 2),
                              EntityLogic=text(row, 3), ModelId=integer(row, 4))
            elif name == 'templateconfig':
                record.update(BlueprintType=text(row, 1), Name=text(row, 2),
                              ComponentsData=json.loads(text(row, 3) or '{}'))
            elif name == 'levelentityconfig':
                record.update(MapId=integer(row, 1), EntityId=integer(row, 2),
                              BlueprintType=text(row, 3), Name=text(row, 4), AreaId=integer(row, 7),
                              ComponentsData=json.loads(text(row, 10) or '{}'))
            elif name == 'abpmontagedata':
                record.update({key: text(row, index) for index, key in enumerate(
                    ('Montage', 'MaleVariant', 'InitState', 'EndState'), 1)})
                record['DisableBlinkCurve'] = flag(row, 5)
            elif name == 'montagedata':
                record.update({key: text(row, index) for index, key in enumerate(
                    ('ActionMontage', 'ExpressionMontage', 'MouthSequence'), 1)})
            elif name == 'speaker':
                record.update({key: text(row, index) for key, index in (
                    ('CameraBindTag', 4), ('HeadIconAsset', 5), ('HeadRoundIconAsset', 6),
                    ('HeadRoundIconAssetMaleVariant', 7), ('RolePileIconAsset', 8))})
                record.update({key: integer(row, index, -1) for key, index in (
                    ('NameStringKey', 1), ('TitleStringKey', 2), ('Name', 3), ('Title', 9))})
            elif name == 'modelconfigpreload':
                # Field offsets follow the client's generated ModelConfigPreload.js.
                record['ActorClassPath'] = text(row, 1) if row.Offset(6) else None
                record.update({key: strings(row, index) for index, key in enumerate(
                    ('ActorClass', 'Animations', 'Effects', 'Audios', 'Meshes', 'Materials',
                     'AnimationBlueprints', 'Others'), 2)})
            else:
                record = decode_shop(blob, name == 'shopfixed')
            record['_client_source'] = {'asset_version': asset_version, 'database': database,
                                        'table': name, 'key': identity,
                                        'row_sha256': hashlib.sha256(blob).hexdigest()}
            yield record


def export_native_sources(config: Path, destination: Path, asset_version: str):
    """Write a fresh, isolated staging tree; never overwrite a datamine checkout."""
    if destination.exists() and any(destination.iterdir()):
        raise ValueError('NPC staging output must be empty')
    destination.mkdir(parents=True, exist_ok=True)
    report = {'asset_version': asset_version, 'tables': []}
    for database, name, logical in TABLES:
        path = destination / logical
        path.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        with path.open('w', encoding='utf-8') as stream:
            stream.write('[')
            for record in native_rows(config, database, name, asset_version):
                stream.write((',' if count else '') + json.dumps(record, ensure_ascii=False))
                count += 1
            stream.write(']\n')
        report['tables'].append({'file': logical, 'rows': count,
                                 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    (destination / 'npc-native-sources.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report
