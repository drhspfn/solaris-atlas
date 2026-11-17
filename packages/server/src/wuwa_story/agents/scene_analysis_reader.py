"""Read logical-scene reports only against a freshly resolved publication plan."""

from sqlalchemy import select

from wuwa_story.db.models.storage import FileReference


async def current_scene_report(session, scene_id: int, asset_version: str, current_plan: dict):
    """Return a complete report for the exact current plan, or None.

    The caller must construct current_plan from the current complete flow and its
    published video references and hashes, not from the report or run metadata.
    """
    if (not isinstance(current_plan, dict) or current_plan.get("version") != 1
            or current_plan.get("cutscene_node_id") != scene_id
            or not isinstance(current_plan.get("segments"), list) or not current_plan["segments"]
            or any(not isinstance(segment, dict) or segment.get("asset_version") != asset_version
                   for segment in current_plan["segments"])):
        return None
    reports = await session.scalars(select(FileReference).where(
        FileReference.owner_node_id == scene_id,
        FileReference.reference_type == "cutscene_visual_description",
        FileReference.metadata_json["asset_version"].astext == asset_version,
        FileReference.metadata_json["complete"].as_boolean().is_(True),
    ).order_by(FileReference.id.desc()))
    for report in reports:
        metadata = report.metadata_json or {}
        if metadata.get("cutscene_node_id") == scene_id and metadata.get("analysis_plan") == current_plan:
            return report
    return None
