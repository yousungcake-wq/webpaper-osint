/* app.js — index.html 과 search.html 이 **함께** 쓰는 것만 모은 파일 (2026-11-17).
 *
 * 왜 있나: 두 페이지가 `kst`·`relTime`·`openSheet`·`closeSheet`·카드 그리기를 각자
 * 갖고 있었고 이미 어긋나 있었다 — index 의 `kst(ts, withDate)` 는 날짜를 뺄 수 있는데
 * search 의 `kst(ts)` 는 항상 날짜를 넣었고, `openSheet` 는 서로 다르게 진화했다.
 * 같은 이름의 함수가 두 벌이면 한쪽만 고쳐지고, 그게 조용한 버그가 된다.
 *
 * 규칙: 이 파일은 "두 페이지에서 똑같아야 하는 것"만 담는다. 페이지 전용 로직(티커·
 * 검색·해시·무한스크롤)은 각 HTML 에 둔다. 로드 순서는 app.css 처럼 app.js → 페이지 스크립트.
 */

/* ── 상수 ─────────────────────────────────────────────────────────── */
const FEED_BASES = [
  './',
  'https://raw.githubusercontent.com/yousungcake-wq/webpaper-osint/main/site/'
];
const PAGE = 40;
const BEAT = { TECH:'테크', ECONOMY:'경제', CONFLICT:'분쟁', POLITICS:'정치', OTHER:'기타' };
const REGION = { US:'북미', LATAM:'중남미', EUROPE:'유럽', ASIA:'아시아',
                 MIDDLE_EAST:'중동', AFRICA:'아프리카', GLOBAL:'국제' };
const BEATS = ['ALL', 'TECH', 'ECONOMY', 'CONFLICT', 'POLITICS', 'OTHER'];
const REGIONS = ['US', 'LATAM', 'EUROPE', 'ASIA', 'MIDDLE_EAST', 'AFRICA', 'GLOBAL'];

const $ = (s, r = document) => r.querySelector(s);
/* 초성만으로 이루어진 질의인가 ('ㅇㄹ' → 이란) */
const isJamo = s => /^[ㄱ-ㅎ]+$/.test(s);
const titleOf = it => it.h || it.t || '';
/* 출처가 길면 카드 폭이 터진다 — 일정 길이에서 자른다 (프랑스 부처명 같은 것) */
const clip = (s, n = 24) => {
  s = s || '';
  return s.length > n ? s.slice(0, n).trimEnd() + '…' : s;
};

/* ── 시간 ─────────────────────────────────────────────────────────── */
function kst(ts, withDate){
  if (!ts) return '';
  const d = new Date(ts);
  if (isNaN(d)) return '';
  const f = new Intl.DateTimeFormat('ko-KR', {
    timeZone:'Asia/Seoul', month:'numeric', day:'numeric',
    hour:'2-digit', minute:'2-digit', hour12:false
  }).formatToParts(d).reduce((o,p)=>(o[p.type]=p.value,o),{});
  const hm = f.hour + ':' + f.minute;
  return withDate ? `${f.month}월 ${f.day}일 ${hm}` : hm;
}
function relTime(ts){
  const d = new Date(ts);
  if (isNaN(d)) return '';
  const m = Math.floor((Date.now() - d.getTime()) / 60000);
  if (m < 1) return '방금';
  if (m < 60) return m + '분 전';
  if (m < 24*60) return Math.floor(m/60) + '시간 전';
  return kst(ts, true);
}
/* 보고 있는 동안에도 "n분 전"이 흐른다 — DOM 은 그대로, 텍스트만 갱신 */
function tickTimes(){
  document.querySelectorAll('time[data-ts]').forEach(el => {
    const v = relTime(el.dataset.ts);
    if (el.textContent !== v) el.textContent = v;
  });
  document.querySelector('.sheet.is-open .sheet__when')?.dispatchEvent(new Event('tick'));
}
setInterval(tickTimes, 30_000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) tickTimes(); });

/* ── 기사 카드 ─────────────────────────────────────────────────────
   tokens 를 주면 제목·요약에 하이라이트한다(검색 페이지). null 이면 그냥 텍스트. */
function setBeat(el, b){
  if (b && BEAT[b]) { el.textContent = BEAT[b]; el.dataset.beat = b; }
  else { el.textContent = ''; delete el.dataset.beat; }
}

function highlightInto(el, text, tokens){
  el.textContent = '';
  const src = text || '';
  if (!tokens || !tokens.length){ el.textContent = src; return; }
  const lower = src.toLowerCase();
  const ranges = [];
  for (const t of tokens){
    if (isJamo(t)) continue;                       // 초성 질의는 위치를 되짚을 수 없다
    let i = lower.indexOf(t);
    while (i !== -1){ ranges.push([i, i + t.length]); i = lower.indexOf(t, i + t.length); }
  }
  if (!ranges.length){ el.textContent = src; return; }
  ranges.sort((a,b) => a[0] - b[0]);
  const merged = [];
  for (const r of ranges){
    const last = merged[merged.length - 1];
    if (last && r[0] <= last[1]) last[1] = Math.max(last[1], r[1]);
    else merged.push(r.slice());
  }
  const frag = document.createDocumentFragment();
  let pos = 0;
  for (const [a, b] of merged){
    if (a > pos) frag.appendChild(document.createTextNode(src.slice(pos, a)));
    const m = document.createElement('mark');
    m.textContent = src.slice(a, b);
    frag.appendChild(m);
    pos = b;
  }
  if (pos < src.length) frag.appendChild(document.createTextNode(src.slice(pos)));
  el.replaceChildren(frag);
}

function makeCard(it, tokens){
  const el = document.createElement('article');
  el.className = 'card';
  el.tabIndex = 0;
  el.setAttribute('role', 'button');
  el.setAttribute('aria-label', titleOf(it));

  const meta = document.createElement('div');
  meta.className = 'card__meta';

  const beatEl = document.createElement('span');
  beatEl.className = 'beat';
  setBeat(beatEl, it.b);

  const where = document.createElement('span');
  where.className = 'card__where';
  where.textContent = [REGION[it.r], clip((it.src && it.src[0]) || '')].filter(Boolean).join(' · ');
  where.title = (it.src && it.src[0]) || '';

  const timeEl = document.createElement('time');
  timeEl.className = 'card__time';
  timeEl.dataset.ts = it.ts || '';
  timeEl.textContent = relTime(it.ts);
  timeEl.dateTime = it.ts || '';
  timeEl.title = kst(it.ts, true);

  meta.append(beatEl, where, timeEl);

  const titleEl = document.createElement('h2');
  titleEl.className = 'card__title';
  highlightInto(titleEl, titleOf(it), tokens);
  if (!it.h) titleEl.lang = 'en';

  const sumEl = document.createElement('p');
  sumEl.className = 'card__summary';
  highlightInto(sumEl, it.summary || '', tokens);

  el.append(meta, titleEl, sumEl);
  el.addEventListener('click', () => openSheet(it));
  el.addEventListener('keydown', e => {
    if (e.key === 'Enter' || e.key === ' '){ e.preventDefault(); openSheet(it); }
  });
  return el;
}

/* ── 피드 받기 ─────────────────────────────────────────────────────
   0쪽(최신 300건) + 이전 쪽(feed-pN.json). 같은 출처 우선, 실패하면 저장소 raw. */
async function fetchFeed(p = 0){
  let err;
  for (const base of FEED_BASES){
    try {
      const r = await fetch(base + (p ? 'feed-p' + p + '.json' : 'feed.json'), {cache:'no-store'});
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const d = await r.json();
      if (!Array.isArray(d.items)) throw new Error('bad payload');
      return d;
    } catch (e){ err = e; }
  }
  throw err || new Error('fetch failed');
}

/* ── 상세 시트 ─────────────────────────────────────────────────────── */
const sheet = $('#sheet');

function openSheet(it){
  setBeat($('.sheet__beat', sheet), it.b);
  const when = $('.sheet__when', sheet);
  const paint = () => {
    when.textContent = [kst(it.ts, true), REGION[it.r], (it.src && it.src[0])].filter(Boolean).join(' · ');
  };
  when.removeEventListener('tick', paint);
  when.addEventListener('tick', paint);
  paint();

  const t = $('.sheet__title', sheet);
  t.textContent = titleOf(it);
  if (!it.h) t.lang = 'en'; else t.removeAttribute('lang');

  const orig = $('.sheet__orig', sheet);
  orig.textContent = it.h && it.t ? it.t : '';
  if (orig.textContent) orig.lang = 'en';
  $('.sheet__body', sheet).textContent = it.summary || '';

  $('.sheet__src', sheet).replaceChildren(...(it.src || []).map(s => {
    const li = document.createElement('li'); li.textContent = s; return li;
  }));

  const cta = $('.sheet__cta', sheet);
  if (it.url){ cta.href = it.url; cta.hidden = false; }
  else { cta.removeAttribute('href'); cta.hidden = true; }

  $('.sheet__inner', sheet).scrollTop = 0;
  sheet.showModal();
  requestAnimationFrame(() => sheet.classList.add('is-open'));
  document.documentElement.style.overflow = 'hidden';
}
function closeSheet(){
  sheet.classList.remove('is-open');
  document.documentElement.style.overflow = '';
  setTimeout(() => { if (!sheet.classList.contains('is-open')) sheet.close(); }, 280);
}
sheet.addEventListener('close', () => {
  sheet.classList.remove('is-open');
  document.documentElement.style.overflow = '';
});
sheet.addEventListener('click', e => { if (e.target === sheet) closeSheet(); });

/* 아래로 끌어 내려 닫기 */
let y0 = 0, dy = 0, drag = false;
sheet.addEventListener('touchstart', e => {
  if ($('.sheet__inner', sheet).scrollTop > 0) return;
  y0 = e.touches[0].clientY; dy = 0; drag = true; sheet.style.transition = 'none';
}, {passive:true});
sheet.addEventListener('touchmove', e => {
  if (!drag) return;
  dy = Math.max(0, e.touches[0].clientY - y0);
  sheet.style.transform = `translateY(${dy}px)`;
}, {passive:true});
sheet.addEventListener('touchend', () => {
  if (!drag) return;
  drag = false;
  sheet.style.transition = ''; sheet.style.transform = '';
  if (dy > 90) closeSheet();
});
