// Shared state for the board: the layout, edit mode with undo, and installed integrations.

import { api } from './api.js';

const UNDO_LIMIT = 50;

export const board = $state({
  revision: 0,
  layout: { groups: [] },
  integrations: {}, // id -> schema
  editing: false,
  dirty: false,
  saving: false,
  error: null,
  undoCount: 0,
});

// Where each tile goes when opened (tile id -> URL), so search can open non-link tiles too.
export const tileLinks = {};

// True while a drag is in progress, so one drag across groups records one undo point.
export const drag = { active: false };

let savedLayout = null;
const undoStack = [];

const snapshot = () => $state.snapshot(board.layout); // already a plain deep copy

export function newId() {
  return crypto.randomUUID?.().replaceAll('-', '').slice(0, 10) ?? Math.random().toString(36).slice(2, 12);
}

export async function load() {
  try {
    const [{ revision, layout }, integrations] = await Promise.all([api.layout(), api.integrations()]);
    board.revision = revision;
    board.layout = layout;
    board.integrations = Object.fromEntries(integrations.map((i) => [i.id, i]));
    board.error = null;
  } catch (e) {
    board.error = e.message;
  }
}

export function startEditing() {
  savedLayout = snapshot();
  undoStack.length = 0;
  board.undoCount = 0;
  board.dirty = false;
  board.editing = true;
}

/** Record an undo point, then apply `fn(layout)`. */
export function change(fn) {
  checkpoint();
  fn(board.layout);
}

/** Record an undo point without changing anything (drag and drop applies its own changes). */
export function checkpoint() {
  undoStack.push(snapshot());
  if (undoStack.length > UNDO_LIMIT) undoStack.shift();
  board.undoCount = undoStack.length;
  board.dirty = true;
}

export function undo() {
  if (!undoStack.length) return;
  board.layout = undoStack.pop();
  board.undoCount = undoStack.length;
  // Compare with the saved layout: the stack is capped, so an empty stack doesn't mean "unchanged".
  board.dirty = JSON.stringify(board.layout) !== JSON.stringify(savedLayout);
}

export function discard() {
  if (savedLayout) board.layout = savedLayout;
  board.editing = false;
  board.dirty = false;
}

export async function save() {
  board.saving = true;
  try {
    const { revision } = await api.saveLayout(snapshot(), board.revision);
    board.revision = revision;
    board.editing = false;
    board.dirty = false;
    board.error = null;
  } catch (e) {
    board.error = e.message;
  } finally {
    board.saving = false;
  }
}

export async function importYaml(text) {
  await api.importLayout(text);
  await load();
  board.editing = false;
  board.dirty = false;
}

export function findTile(tileId) {
  for (const group of board.layout.groups) {
    const tile = group.tiles.find((t) => t.id === tileId);
    if (tile) return { group, tile };
  }
  return null;
}
