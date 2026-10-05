import { storyExplanation } from './storyExplanation.mjs';

export const readingExplanation = {
  ...storyExplanation,
  title: 'An arrival with unanswered questions',
  links: storyExplanation.links.map((link) => ({
    ...link,
    href: '/story-analysis/connections/1/0?locale=en',
    certainty: 'inferred',
    explanation:
      'The opening encounter connects Rover with the people who find them. This illustrative description keeps the encounter separate from any explanation of Rover’s origin.',
  })),
  blocks: [
    {
      ...storyExplanation.blocks[0],
      title: 'What happens',
      text: 'The opening brings Rover into [an encounter with the people who find them](connection:0). Their condition raises a question: **the appearance of drowning and the observations made during the examination are different claims.**\n\nThe examination provides an anchor for the scene, but it does not establish the cause of Rover’s arrival.',
    },
    {
      ...storyExplanation.blocks[0],
      title: 'Why the examination matters',
      text: 'The observations give the player something concrete to work with. They establish Rover’s condition at the encounter while leaving the preceding events unexplained.\n\n- An observed condition is evidence.\n- A possible cause remains an interpretation.\n- [The original examination](record:1) can be read in the quest.',
    },
  ],
};
