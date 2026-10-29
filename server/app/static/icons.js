// Иконки серверов — общие для экрана платы и /admin. Контурные, 24×24, цвет = currentColor.
const SERVER_ICONS = {
  server:  ["Сервер",    '<rect x="4" y="3" width="16" height="7" rx="1.5"/><rect x="4" y="14" width="16" height="7" rx="1.5"/><path d="M8 6.5h.01M8 17.5h.01M12 6.5h4M12 17.5h4"/>'],
  home:    ["Домашний",  '<path d="M3 11 12 4l9 7"/><path d="M5.5 9.5V20h13V9.5"/><path d="M10 20v-5.5h4V20"/>'],
  work:    ["Рабочий",   '<rect x="3" y="7.5" width="18" height="12.5" rx="2"/><path d="M9 7.5V5.5a1.5 1.5 0 0 1 1.5-1.5h3A1.5 1.5 0 0 1 15 5.5v2M3 13h18"/>'],
  prod:    ["Прод",      '<path d="M12 3c3 2.5 4.5 6 4 10l-4 3-4-3c-.5-4 1-7.5 4-10z"/><circle cx="12" cy="10" r="1.6"/><path d="M8 13l-3 2.5V19l3.5-2M16 13l3 2.5V19l-3.5-2M10.5 19.5 12 21l1.5-1.5"/>'],
  test:    ["Тест",      '<path d="M9.5 3h5M10.5 3v6L5 19a1.5 1.5 0 0 0 1.3 2h11.4a1.5 1.5 0 0 0 1.3-2L13.5 9V3"/><path d="M7.5 15h9"/>'],
  db:      ["База",      '<ellipse cx="12" cy="5.5" rx="7.5" ry="2.5"/><path d="M4.5 5.5v13c0 1.4 3.4 2.5 7.5 2.5s7.5-1.1 7.5-2.5v-13M4.5 12c0 1.4 3.4 2.5 7.5 2.5s7.5-1.1 7.5-2.5"/>'],
  web:     ["Сайт",      '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.6 3.7 5.6 3.7 9s-1.2 6.4-3.7 9c-2.5-2.6-3.7-5.6-3.7-9S9.5 5.6 12 3z"/>'],
  cloud:   ["Облако",    '<path d="M7 19h10.5a4 4 0 0 0 .6-7.95A6 6 0 0 0 6.6 9.6 4.75 4.75 0 0 0 7 19z"/>'],
  nas:     ["Хранилище", '<rect x="5" y="3" width="14" height="18" rx="2"/><circle cx="12" cy="10" r="3.5"/><path d="M12 10h.01M8.5 17.5h7"/>'],
  router:  ["Роутер",    '<rect x="3" y="13" width="18" height="7" rx="2"/><path d="M7 16.5h.01M10.5 16.5h.01M15 13l2-6M8.5 6.5a5 5 0 0 1 7 0M6.5 4a8 8 0 0 1 11 0"/>'],
  pc:      ["Компьютер", '<rect x="3" y="4" width="18" height="12" rx="1.5"/><path d="M9 20h6M12 16v4"/>'],
  docker:  ["Контейнеры",'<path d="M3 12h15.5c1.2 0 2.2-.6 2.5-1.5M3 12c0 4.4 3.2 7.5 8 7.5 5.2 0 8.6-3.6 9.5-8.5"/><rect x="5" y="8" width="3" height="3"/><rect x="8.5" y="8" width="3" height="3"/><rect x="12" y="8" width="3" height="3"/><rect x="8.5" y="4.5" width="3" height="3"/>'],
  game:    ["Игровой",   '<path d="M7 8h10a4 4 0 0 1 4 4.5l-.6 3.6a2.3 2.3 0 0 1-4 1L15 15.5H9l-1.4 1.6a2.3 2.3 0 0 1-4-1L3 12.5A4 4 0 0 1 7 8z"/><path d="M7.5 11v3M6 12.5h3M15.5 12h.01M17.5 13.5h.01"/>'],
  camera:  ["Камера",    '<rect x="3" y="7" width="13" height="10" rx="2"/><path d="m16 11 5-3v8l-5-3"/>'],
  shield:  ["Защита",    '<path d="M12 3 5 6v5.5c0 4.5 3 8 7 9.5 4-1.5 7-5 7-9.5V6z"/><path d="m9 12 2 2 4-4"/>'],
  bolt:    ["Питание",   '<path d="M13 3 5 13.5h6L10 21l8-10.5h-6z"/>'],
};
function serverIcon(key, size = 24) {
  const ic = SERVER_ICONS[key] || SERVER_ICONS.server;
  return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${ic[1]}</svg>`;
}
