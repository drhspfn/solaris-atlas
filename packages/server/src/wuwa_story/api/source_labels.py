"""Human-readable labels for source tables shown in browsing responses."""

SOURCE_FAMILY_LABELS = {
    "activity/roletrialinfo.json": "Trial activity settings",
    "activity/roletrialroleconfig.json": "Trial character settings",
    "audio/roleinfolang.json": "Voice and audio settings",
    "Birthday/rolebirthday.json": "Birthday content",
    "cook/specialcook.json": "Signature dish settings",
    "favor/favorgoods.json": "Favorite gifts",
    "favor/favorroleinfo.json": "Affinity profile",
    "favor/favorstory.json": "Affinity stories",
    "favor/favorword.json": "Affinity voice lines",
    "accesspath/accesspath.json": "Item acquisition paths",
    "enrichment/enrichmentareaconfig.json": "World gathering configurations",
    "GachaRoleDevelop/gacharoledevelopins.json": "Character development settings",
    "GongduolaPassengerVoiceConfig/gongduolapassengervoiceconfig.json": "Passenger voice lines",
    "RoleTrial/roletrialinfo.json": "Trial activity settings",
    "RoleTrial/roletrialroleconfig.json": "Trial character settings",
    "QuestNodeData/questnodedata.json": "Quest objectives and conditions",
}


def source_family_label(source_file: str | None) -> str:
    if not source_file:
        return "Other game configuration"
    relative = source_file.removeprefix("BinData/")
    return SOURCE_FAMILY_LABELS.get(relative, "Other game configuration")
