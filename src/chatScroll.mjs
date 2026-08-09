export const distanceFromChatBottom = ({ scrollHeight, scrollTop, clientHeight }) =>
    scrollHeight - scrollTop - clientHeight;

export const isNearChatBottom = (element, threshold = 72) =>
    distanceFromChatBottom(element) < threshold;
