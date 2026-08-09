export const chatSender = (role) => role === 'user' ? 'MARCELO' : role === 'assistant' ? 'VERÔNICA' : 'SYSTEM';

export const chatMessage = (message) => {
    const timestamp = message.timestamp || new Date().toISOString();
    return {
        id: message.id,
        role: message.role,
        sender: message.sender || chatSender(message.role),
        text: message.content ?? message.text ?? '',
        timestamp,
        time: new Date(timestamp).toLocaleTimeString(),
        source: message.source || 'system',
        streaming: Boolean(message.streaming),
        sequence: Number.isFinite(message.sequence) ? message.sequence : null
    };
};

export const mergeChatMessages = (current, incoming) => {
    const byId = new Map(current.map(message => [message.id, message]));
    incoming.forEach(message => {
        const normalized = chatMessage(message);
        byId.set(normalized.id, { ...byId.get(normalized.id), ...normalized });
    });
    return [...byId.values()].sort((left, right) =>
        String(left.timestamp).localeCompare(String(right.timestamp)) ||
        ((left.sequence ?? Number.MAX_SAFE_INTEGER) - (right.sequence ?? Number.MAX_SAFE_INTEGER)) ||
        String(left.id).localeCompare(String(right.id))
    );
};

export const newChatId = () => globalThis.crypto?.randomUUID?.() || `chat-${Date.now()}-${Math.random().toString(16).slice(2)}`;
