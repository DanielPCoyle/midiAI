// tmux capture-pane hard-wraps every line to the PANE's width -- 39 columns
// in a split -- so one sentence arrives as four lines. Rendered a line at a
// time that becomes four paragraphs, and the answer reads like a ransom note.
//
// Joined back into the paragraph it was, unless the line genuinely starts
// something: a bullet, a heading, a numbered item, a fence, or a blank line
// before it. Those are the only breaks the agent actually wrote; every other
// newline in a capture belongs to the terminal, not to the prose.
export default function reflow(rows) {
  const out = [];
  let blank = false;
  let closed = false;
  for (const row of rows) {
    const text = String(row || '').trim();
    if (!text) { blank = true; continue; }
    // a bullet or a numbered item BREAKS before itself and then keeps taking
    // wrapped lines; a heading, a fence or a table row also CLOSES, because
    // nothing that follows one is a continuation of it
    // a short line in shouty capitals on its own is a heading -- TLDR,
    // PROGRESS, NEXT. Agents here write them constantly and without this one
    // they get swallowed into the end of the sentence above: "(exit 0). TLDR"
    const shout = /^[A-Z][A-Z0-9 ]{1,14}$/.test(text);
    const starts = shout || /^([-*•]\s|#{1,3}\s|\d+[.)]\s|```|\|)/.test(text);
    if (!out.length || blank || starts || closed) out.push(text);
    else out[out.length - 1] += ` ${text}`;
    closed = shout || /^(#{1,3}\s|```|\|)/.test(text);
    blank = false;
  }
  return out.join('\n');
}
