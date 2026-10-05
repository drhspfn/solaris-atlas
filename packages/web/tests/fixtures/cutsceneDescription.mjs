export const cutsceneEvent = {
  kind: 'cutscene',
  reference: 'cutscene:Arrival',
  playback: {
    entry: 'scene',
    asset_version: '3.7.0',
    evidence: 'Fixture source',
    nodes: [{ id: 'scene', kind: 'clip', asset: 'scene', start: 0, end: 120, next: null }],
    media: {
      scene: {
        url: 'data:video/mp4,',
        asset_node_id: 5,
        has_audio: false,
        description: {
          title: 'Arrival at the shore',
          text: 'A figure approaches the shoreline as the view opens onto the city.',
          chapters: [
            {
              start: 30,
              end: 90,
              title: 'The shoreline',
              text: 'The scene shifts to the waterfront.',
            },
          ],
        },
      },
    },
  },
};
