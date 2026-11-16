// The demo page in an iframe of the suite's page (same origin: the test reads and drives its DOM).
export function load(src: string): Promise<Document> {
  const frame = document.createElement('iframe');
  frame.width = '600';
  frame.height = '800';
  document.body.append(frame);
  return new Promise((resolve, reject) => {
    frame.addEventListener('load', () => resolve(frame.contentDocument!), { once: true });
    frame.addEventListener('error', () => reject(new Error(`cannot load ${src}`)), { once: true });
    frame.src = src;
  });
}
