// One palette for the whole app. The Push has its own 128-entry palette and
// no screen can see it; these are the browser-side approximations the old
// editor used, kept identical so a pad looks the same in both places.
export const C = {
  bg: '#0b0b0d',
  panel: '#101014',
  raised: '#16161c',
  line: '#24242a',
  edge: '#33333c',
  text: '#e6e6e6',
  dim: '#9aa0aa',
  faint: '#5b616b',
  accent: '#3d5a80',
  accentText: '#7aa2d2',
  good: '#7fb37f',
  warn: '#e0a02c',
  bad: '#e08a8a',
};

// keyed by the Push's own colour numbers, so a pad is the same colour in both
// places. macros.json was remapped to these in the same commit.
export const PAD_HEX = {
  120: '#e03c3c', // red
  60: '#e08a2c', // orange
  13: '#e0d02c', // yellow
  21: '#3cd05a', // green
  33: '#2cd0d0', // cyan
  45: '#4a86d0', // blue
  49: '#6a5ae0', // indigo
  53: '#b04ae0', // violet
  3: '#e6e6e6', // white
};
export const PALETTE = [
  ['red', 120],
  ['orange', 60],
  ['yellow', 13],
  ['green', 21],
  ['cyan', 33],
  ['blue', 45],
  ['indigo', 49],
  ['violet', 53],
  ['white', 3],
];
export const hexFor = (colour) => PAD_HEX[colour] || PAD_HEX[45];

// push_cc.ANSWER_CC, in the browser's approximations of the same palette --
// derived from PAD_HEX rather than retyped, so the pad you press on the Push
// and the row you press here cannot drift into different colours.
export const ANSWER_HEX = [
  PAD_HEX[126], // green
  PAD_HEX[125], // blue
  PAD_HEX[8], // yellow
  PAD_HEX[3], // orange
  PAD_HEX[127], // red
  PAD_HEX[122], // white
];

// agent_status, in the same hues the pads and the Push screen use.
// `unknown` is a claude whose process is there but whose session has not
// started -- present, unclassified. It was falling through to C.faint by
// accident; grey is the right answer, but it should be chosen.
export const SEAT_HEX = {
  idle: '#3cd05a',
  working: '#f0c828',
  blocked: '#f03c3c',
  unknown: '#6b7280',
};

// The chassis, off the photographs: the buttons sit darker than the panel
// around them, ringed by a hairline that catches light along the top edge.
export const KEY = {
  face: '#08080a',
  edge: '#1d1d23',
  top: '#2c2c35',
  lit: '#101016',
};

export const S = {
  gap: 8,
  pad: 14,
  radius: 8,
  hit: 44, // the smallest thing a finger should have to find
  control: 40, // header controls -- shorter than a pad, still a finger target
};

export const mono = {
  fontFamily: 'Menlo',
};
