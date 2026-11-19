for (const cs of ['UAL1310', 'DAL1', 'AAL100', 'BAW117', 'UPS80']) {
  const t0 = Date.now();
  const r = await fetch(`https://api.adsbdb.com/v0/callsign/${cs}`, { headers: { 'User-Agent': 'YashHoodaPortfolio/1.0' } }).catch(e => ({ status: 'ERR ' + e.message, text: async () => '', headers: new Map() }));
  const body = await r.text();
  console.log('ADSBDB', cs, r.status, Date.now() - t0 + 'ms', r.headers.get ? (r.headers.get('x-ratelimit-limit') || r.headers.get('ratelimit-limit') || '') : '', body.slice(0, 420));
}
