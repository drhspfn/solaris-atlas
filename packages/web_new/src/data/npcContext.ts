/** Display labels only; technical source identities stay in API responses. */
export function conversationSpeakerName(label?: string): string {
  const value = (label || '').replace(/\{message\}/gi, '').trim();
  if (/\{PlayerName\}/i.test(value)) return 'Rover';
  if (!value || /^[?？.\s]+$/.test(value) || /^(speaker|character):/i.test(value)) return 'Unknown speaker';
  return value;
}
