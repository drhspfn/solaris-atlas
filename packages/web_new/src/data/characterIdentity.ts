export type CharacterVariant = {
  canonical_key: string;
  gender: string;
  element_id: number;
  element: unknown;
};

/** Change one coordinate without silently changing the other. */
export function selectCharacterVariant(
  variants: CharacterVariant[],
  current: CharacterVariant,
  change: { gender?: string; element_id?: number },
): CharacterVariant | undefined {
  return variants.find((variant) =>
    variant.gender === (change.gender ?? current.gender) &&
    variant.element_id === (change.element_id ?? current.element_id));
}
