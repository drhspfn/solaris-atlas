import assert from 'node:assert/strict';
import { test } from 'node:test';

import { preferredRoverTarget } from '../src/components/story/preferredRover.ts';

const choice = {
  kind: 'choice',
  options: [
    { label: 'A', next: 'male-clip', rover: 'male' },
    { label: 'B', next: 'female-clip', rover: 'female' },
  ],
};

test('uses explicit Rover identity independent of labels', () => {
  assert.equal(preferredRoverTarget(choice, 'male'), 'male-clip');
  assert.equal(preferredRoverTarget(choice, 'female'), 'female-clip');
  assert.equal(preferredRoverTarget(choice, 'ask'), null);
});

test('does not skip narrative choices or incomplete Rover pairs', () => {
  assert.equal(
    preferredRoverTarget(
      { ...choice, options: choice.options.map(({ rover, ...o }) => o) },
      'female',
    ),
    null,
  );
  assert.equal(
    preferredRoverTarget({ ...choice, options: [choice.options[0], choice.options[0]] }, 'male'),
    null,
  );
  assert.equal(
    preferredRoverTarget(
      { ...choice, options: [...choice.options, { label: 'Other', next: 'other' }] },
      'female',
    ),
    null,
  );
  assert.equal(preferredRoverTarget({ kind: 'clip' }, 'female'), null);
});
