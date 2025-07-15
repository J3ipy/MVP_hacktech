const CACHE_NAME = 'patrimonio-cache-v1';
// Lista de arquivos essenciais para o funcionamento offline do app.
const urlsToCache = [
  '/',
  '/patrimonios',
  // Adicione aqui os caminhos para seus principais arquivos CSS e JS se os tiver separado.
  // O service worker não pode cachear requisições POST, então a API não entra aqui.
];

// Evento de Instalação: Salva os arquivos essenciais no cache.
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => {
        console.log('Cache aberto');
        return cache.addAll(urlsToCache);
      })
  );
});

// Evento de Fetch: Intercepta as requisições.
// Tenta servir do cache primeiro, se não conseguir, busca na rede.
self.addEventListener('fetch', event => {
  event.respondWith(
    caches.match(event.request)
      .then(response => {
        // Se encontrar no cache, retorna a resposta do cache
        if (response) {
          return response;
        }
        // Se não, busca na rede
        return fetch(event.request);
      }
    )
  );
});
