const { default: handler } = await import('./flights.mjs');
const run = async () => { let out = {}; const res = { setHeader() {}, status(c) { out.code = c; return { json: (j) => (out.body = j, out), end: () => out }; } }; await handler({ method: 'GET' }, res); return out; };
const t0 = Date.now(); const r = await run();
const b = r.body;
console.log('RESULT', r.code, 'source', b.source, 'total', b.total, 'shown', b.shown, 'ms', Date.now() - t0, 'err', b.error || '', JSON.stringify(b.detail || ''));
const withRoute = (b.flights || []).filter(f => f.origin_iata || f.dest_iata).length;
console.log('ROUTES', withRoute, 'of', (b.flights || []).length);
console.log('SAMPLE', JSON.stringify((b.flights || []).find(f => f.origin_iata) || (b.flights || [])[0]));
const lons = (b.flights || []).map(f => f.lon); console.log('SPREAD lon', Math.min(...lons).toFixed(0), Math.max(...lons).toFixed(0));
