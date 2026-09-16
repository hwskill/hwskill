export function highlightSegments(text, terms) {
  const needles = [...new Set(terms.map((term) => term.trim().toLowerCase()).filter(Boolean))];
  const lowerText = text.toLowerCase();
  const segments = [];
  let offset = 0;
  while (offset < text.length && needles.length) {
    const next = needles
      .map((needle) => ({ index: lowerText.indexOf(needle, offset), needle }))
      .filter(({ index }) => index !== -1)
      .sort((left, right) => left.index - right.index || right.needle.length - left.needle.length)[0];
    if (!next) break;
    if (next.index > offset) segments.push({ text: text.slice(offset, next.index), match: false });
    const end = next.index + next.needle.length;
    segments.push({ text: text.slice(next.index, end), match: true });
    offset = end;
  }
  if (offset < text.length || !segments.length) segments.push({ text: text.slice(offset), match: false });
  return segments;
}
