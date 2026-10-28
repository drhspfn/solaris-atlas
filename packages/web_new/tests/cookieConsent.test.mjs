import assert from 'node:assert/strict';
import test from 'node:test';
import { COOKIE_CHOICE_KEY, readCookieChoice, saveCookieChoice } from '../src/preferences/cookieConsent.ts';

test('cookie choice is explicit, persistent, revocable and fails closed', () => {
  const values = new Map();
  const store = { getItem: (key) => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) };
  assert.equal(readCookieChoice(store), null);
  assert.equal(saveCookieChoice(store, 'all'), true);
  assert.equal(readCookieChoice(store), 'all');
  assert.equal(saveCookieChoice(store, 'essential'), true);
  assert.equal(readCookieChoice(store), 'essential');
  values.set(COOKIE_CHOICE_KEY, 'invalid');
  assert.equal(readCookieChoice(store), null);
  assert.equal(readCookieChoice({ getItem() { throw Error('denied'); } }), null);
  assert.equal(saveCookieChoice({ setItem() { throw Error('denied'); } }, 'all'), false);
});
