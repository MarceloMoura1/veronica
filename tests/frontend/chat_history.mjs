import assert from 'node:assert/strict';
import { chatMessage, mergeChatMessages } from '../../src/chatHistory.mjs';
import { isNearChatBottom } from '../../src/chatScroll.mjs';

const older = chatMessage({ id: 'older', role: 'user', content: 'Antiga', timestamp: '2026-01-01T10:00:00Z', source: 'voice' });
const newer = chatMessage({ id: 'newer', role: 'assistant', content: 'Nova', timestamp: '2026-01-01T10:00:01Z', source: 'assistant' });
assert.deepEqual(mergeChatMessages([], [newer, older]).map(item => item.id), ['older', 'newer']);

const partial = chatMessage({ id: 'stream', role: 'assistant', content: 'Olá', timestamp: '2026-01-01T10:00:02Z', source: 'assistant', streaming: true });
const final = chatMessage({ id: 'stream', role: 'assistant', content: 'Olá, Marcelo.', timestamp: '2026-01-01T10:00:02Z', source: 'assistant' });
const merged = mergeChatMessages([partial], [final]);
assert.equal(merged.length, 1);
assert.equal(merged[0].text, 'Olá, Marcelo.');
assert.equal(merged[0].streaming, false);

assert.equal(isNearChatBottom({ scrollHeight: 1000, scrollTop: 730, clientHeight: 220 }), true);
assert.equal(isNearChatBottom({ scrollHeight: 1000, scrollTop: 300, clientHeight: 220 }), false);

console.log('frontend chat history tests: passed');
