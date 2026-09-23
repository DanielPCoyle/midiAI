// One runnable check for the one piece of non-trivial parsing in the pretty
// view. `node app/reflow_check.mjs` -- no runner, no fixtures. It imports the
// same function the app does, so the rule cannot drift away from its test.
import reflow from './src/reflow.js';

let bad = 0;
const eq = (got, want, why) => {
  if (got === want) return console.log('ok —', why);
  bad += 1;
  console.error('FAIL:', why, '\n  got: ', JSON.stringify(got), '\n  want:', JSON.stringify(want));
};

eq(reflow(['The sleep finished and printed done', '(exit 0).']),
   'The sleep finished and printed done (exit 0).',
   'a sentence tmux wrapped is still one sentence');
eq(reflow(['First para.', '', 'Second para.']), 'First para.\nSecond para.',
   'a blank line is the one newline in a capture that means what it says');
eq(reflow(['- Progress: ls -la gave 147', 'entries in the repo root', '- Next: nothing pending']),
   '- Progress: ls -la gave 147 entries in the repo root\n- Next: nothing pending',
   'a wrapped bullet stays one bullet; the next bullet still breaks');
eq(reflow(['## Heading', 'body text', 'more body']), '## Heading\nbody text more body',
   'a heading breaks before itself AND closes after it');
eq(reflow(['1. one', 'wrapped', '2. two']), '1. one wrapped\n2. two',
   'numbered items break');
eq(reflow(['| a | b |', '| c | d |']), '| a | b |\n| c | d |',
   'table rows are not prose and never join');
eq(reflow([]), '', 'nothing in, nothing out');
eq(reflow(['', '  ', '']), '', 'only blanks is still nothing');
eq(reflow(['```', 'code()', '```']), '```\ncode()\n```', 'a fence closes, so its body stays put');
eq(reflow(['The sleep finished (exit 0).', 'TLDR', '- Progress: done']),
   'The sleep finished (exit 0).\nTLDR\n- Progress: done',
   'a shouty one-word line is a heading, not the end of the sentence above');
eq(reflow(['a sentence', 'I SHOUTED A VERY LONG LINE INDEED here']),
   'a sentence I SHOUTED A VERY LONG LINE INDEED here',
   'long or mixed-case is prose, not a heading');

console.log(bad ? `\n${bad} failed` : '\nreflow: ok');
process.exit(bad ? 1 : 0);
