import json

from wuwa_story_worker.compiler.core import Writer
from wuwa_story_worker.compiler.entities import compile_entities


def test_weapons_are_items_with_exact_ids_localized_names_and_source(tmp_path):
    base=tmp_path/'source/BinData'
    (base/'item').mkdir(parents=True)
    (base/'weapon').mkdir()
    subtitles=tmp_path/'source/Textmaps/en/subtitle_text/SubtitleText.json'
    subtitles.parent.mkdir(parents=True)
    subtitles.write_text('[]')
    (base/'item/iteminfo.json').write_text(json.dumps([{'Id':3,'Name':'Astrite'}]))
    (base/'weapon/weaponconf.json').write_text(json.dumps([
        {'ItemId':21020107,'WeaponName':'WeaponConf_21020107_WeaponName','BgDescription':'weapon_bg','Icon':'/Game/Weapon'},
        {'ItemId':3,'WeaponName':'duplicate'}]))
    out=tmp_path/'compiled'
    writer=Writer(out,'3.7.0')
    counts=compile_entities(tmp_path/'source',writer,{'WeaponConf_21020107_WeaponName':{'content':'Unspoken Rue'},'weapon_bg':{'content':'Description'}})
    writer.finish()
    items=[json.loads(line) for line in (out/'entities/item.jsonl').read_text().splitlines()]
    assert counts['item']==2
    weapon=next(r for r in items if r['id']=='item:21020107')
    assert weapon['name']['en']=='Unspoken Rue'
    assert weapon['source']['file']=='BinData/weapon/weaponconf.json'
    assert weapon['raw']['Icon']=='/Game/Weapon'
