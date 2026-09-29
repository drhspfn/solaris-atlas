# PlotAudio records without direct TalkItem references

The 3.6 snapshot contains 40,597 PlotAudio records. The direct exact join is unchanged: `TalkItem.TidTalk == PlotAudio.Id`. It yields 40,701 source occurrences across 40,182 distinct PlotAudio IDs; 415 PlotAudio IDs have no incoming TalkItem reference. Coverage distinguishes occurrences from unique IDs.

Unused IDs were grouped by naming prefixes (largest groups include Main 135, HD 61, CommonVoice 53, Event 32, Heihaian 28, Character 23 and NPC 21). `ExternalSourceSetting` is predominantly `subtitle_normal` (40,541 records overall) with small `bubble_hard_ducking`/`bubble_soft_ducking` groups. These are metadata categories, not proof of a dialogue subsystem. FileName values often have a `vo_` prefix and 432 FileName values are duplicated, so filename resemblance is not an identity join.

Exact value collisions in PlotLineKey/TidTalkOption/center-text/caption text namespaces are localization identities, not proven audio relations. No new PlotAudio edges were added from those matches. Current evidence cannot classify the 415 IDs as dead, cut, ambient, battle or interaction dialogue; they are reported as unreferenced by the supported direct TalkItem join.
