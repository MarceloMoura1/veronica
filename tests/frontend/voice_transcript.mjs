import assert from 'node:assert/strict';
import { chatMessage, mergeChatMessages } from '../../src/chatHistory.mjs';
import { applyTranscriptionEvent } from '../../src/voiceTranscript.mjs';

const event = (id, role, text, final = false, sequence = 1) => ({
    message_id: id, turn_id: id, role, text, final, sequence,
    source: role === 'user' ? 'input_transcription' : 'output_transcription',
    timestamp: `2026-01-01T10:00:0${sequence}Z`,
});

let messages = [chatMessage({ id: 'system-1', role: 'system', content: 'Found 0 Kasa devices', timestamp: '2026-01-01T10:00:00Z' })];
for (const item of [
    event('user-1', 'user', 'Verônica, está me ouvindo?', true, 1),
    event('assistant-1', 'assistant', 'Sim, Chefe. Estou ouvindo perfeitamente.', true, 2),
    event('user-2', 'user', 'Beleza.', true, 3),
    event('assistant-2', 'assistant', 'Tudo certo por aqui.', true, 4),
]) messages = applyTranscriptionEvent(messages, item);
assert.equal(messages.length, 5);
assert.deepEqual(messages.map(item => item.role), ['system', 'user', 'assistant', 'user', 'assistant']);

let userPartial = [];
userPartial = applyTranscriptionEvent(userPartial, event('partial-user', 'user', 'Verônica está', false));
userPartial = applyTranscriptionEvent(userPartial, event('partial-user', 'user', 'Verônica está me ouvindo?', false));
assert.equal(userPartial.length, 1);
assert.equal(userPartial[0].text, 'Verônica está me ouvindo?');
assert.equal(userPartial[0].streaming, true);

let assistantPartial = [];
assistantPartial = applyTranscriptionEvent(assistantPartial, event('partial-assistant', 'assistant', 'Tudo', false));
assistantPartial = applyTranscriptionEvent(assistantPartial, event('partial-assistant', 'assistant', 'Tudo certo.', true));
assert.equal(assistantPartial.length, 1);
assert.equal(assistantPartial[0].text, 'Tudo certo.');
assert.equal(assistantPartial[0].streaming, false);

const immutable = applyTranscriptionEvent(assistantPartial, event('partial-assistant', 'assistant', 'partial atrasado', false));
assert.equal(immutable[0].text, 'Tudo certo.');

const withSystem = mergeChatMessages(assistantPartial, [chatMessage({ id: 'printer', role: 'system', content: 'Printer connected', timestamp: '2026-01-01T10:00:02Z' })]);
assert.equal(withSystem.find(item => item.id === 'partial-assistant').text, 'Tudo certo.');

const toolIgnored = applyTranscriptionEvent(withSystem, { ...event('tool', 'assistant', 'segredo'), source: 'tool' });
const thoughtIgnored = applyTranscriptionEvent(toolIgnored, { ...event('thought', 'assistant', 'raciocínio'), source: 'thought' });
assert.equal(thoughtIgnored.length, withSystem.length);

const wrongOwner = applyTranscriptionEvent(userPartial, event('partial-user', 'assistant', 'mistura', true));
assert.equal(wrongOwner[0].role, 'user');

const restored = mergeChatMessages(messages, messages.map(item => ({ id: item.id, role: item.role, content: item.text, timestamp: item.timestamp, source: item.source })));
assert.equal(restored.length, 5);
assert.equal(restored.filter(item => item.role === 'system').length, 1);

assert.equal(messages.find(item => item.id === 'user-2').sender, 'MARCELO');
assert.equal(messages.find(item => item.id === 'assistant-2').sender, 'VERÔNICA');
console.log('frontend voice transcript tests: passed');
