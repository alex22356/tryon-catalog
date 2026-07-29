# Захват ссылок одним кликом (браузер → shein_links.txt)

Ты сам открываешь категорию в своём браузере (как обычный человек), а этот сниппет
собирает все товарные ссылки со страницы и кладёт в буфер обмена.
Дальше `pipeline.py` делает всё остальное автономно.

## Вариант A — закладка (удобнее всего, 1 клик)

Создай закладку в браузере, в поле «Адрес» вставь **весь** этот код:

```
javascript:(async()=>{const S=new Set();const G=()=>{document.querySelectorAll('a[href*="-p-"]').forEach(a=>{const m=a.href.match(/(https:\/\/[^?#]*?-p-\d+\.html)/);if(m)S.add(m[1])})};G();let last=0,idle=0;const box=document.createElement('div');box.style.cssText='position:fixed;z-index:999999;right:16px;bottom:16px;background:#111;color:#0f0;font:14px monospace;padding:10px 14px;border-radius:8px;box-shadow:0 4px 16px rgba(0,0,0,.4)';document.body.appendChild(box);while(idle<4){window.scrollBy(0,3000);await new Promise(r=>setTimeout(r,1200));G();box.textContent='собрано: '+S.size;if(S.size===last){idle++}else{idle=0;last=S.size}}const t=[...S].join('\n');try{await navigator.clipboard.writeText(t);box.textContent='✓ '+S.size+' ссылок в буфере';}catch(e){const ta=document.createElement('textarea');ta.value=t;document.body.appendChild(ta);ta.select();document.execCommand('copy');ta.remove();box.textContent='✓ '+S.size+' ссылок в буфере';}setTimeout(()=>box.remove(),6000)})()
```

Как пользоваться:
1. Открой любую категорию SHEIN в браузере (женская одежда, обувь, платья…).
2. Нажми закладку → страница сама прокрутится, счётчик покажет прогресс.
3. Когда напишет «✓ N ссылок в буфере» — открой `shein_links.txt` и вставь (Ctrl+V).
4. Запусти `python scripts/pipeline.py` — дальше всё само.

## Вариант B — консоль

F12 → Console → вставь тот же код без префикса `javascript:` → Enter.

## Заметки

- Работает на любой странице SHEIN со списком товаров: категория, поиск, «Best Seller»
  в affiliate-кабинете, твои «Picks», страница бренда.
- Собирает **обычные** ссылки на товар. Партнёрские (onelink) — через Convert Link
  в кабинете, либо позже автоматически из фида, когда одобрят.
- Дубли не страшны: `pipeline.py` отсеивает уже добавленные.
