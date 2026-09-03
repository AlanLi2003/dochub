/**
 * DocHub 前端交互脚本
 * 包含：搜索联想、产品分组展开、收藏、阅读进度、回到顶部
 * 左侧筛选（文档类型 / 品牌）为单选，直接使用服务端渲染的链接跳转，无需 JS 拼装 URL
 */

document.addEventListener('DOMContentLoaded', function () {
    initSearchSuggest();
    initGroupToggle();
    initFavoriteButtons();
    initReadingProgress();
    initBackToTop();
    initFlashDismiss();
});

// ============================================================
// 搜索联想（所有搜索框共用，防抖请求 /api/search/suggest）
// ============================================================
function initSearchSuggest() {
    let debounceTimer = null;
    let currentDropdown = null;

    document.querySelectorAll('input[name="q"]').forEach(function (input) {
        const wrapper = input.closest('.search-input-wrap') || input.parentElement;
        if (!wrapper) return;

        input.addEventListener('input', function () {
            clearTimeout(debounceTimer);
            const q = input.value.trim();
            if (!q) {
                removeDropdown();
                return;
            }
            debounceTimer = setTimeout(function () {
                fetchSuggest(q, wrapper, input);
            }, 250);
        });

        input.addEventListener('focus', function () {
            if (input.value.trim() && currentDropdown) currentDropdown.classList.add('show');
        });

        document.addEventListener('click', function (e) {
            if (!wrapper.contains(e.target)) removeDropdown();
        });
    });

    function fetchSuggest(q, wrapper, input) {
        fetch('/api/search/suggest?q=' + encodeURIComponent(q))
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (!data.results || !data.results.length) {
                    removeDropdown();
                    return;
                }
                renderDropdown(data.results, wrapper, input);
            })
            .catch(function () { removeDropdown(); });
    }

    function renderDropdown(results, wrapper, input) {
        removeDropdown();
        const dropdown = document.createElement('div');
        dropdown.className = 'search-suggest-dropdown';
        results.forEach(function (item) {
            const option = document.createElement('a');
            option.className = 'suggest-item';
            // 联想结果按产品聚合：点击直接搜索该产品全称
            option.href = '/search?q=' + encodeURIComponent(item.product_name || item.title);
            const brand = item.brand_name
                ? '<span class="suggest-brand">' + escapeHtml(item.brand_name) + '</span>' : '';
            option.innerHTML = brand +
                '<span class="suggest-title">' + escapeHtml(item.product_name || item.title) + '</span>';
            dropdown.appendChild(option);
        });
        wrapper.appendChild(dropdown);
        currentDropdown = dropdown;
        requestAnimationFrame(function () { dropdown.classList.add('show'); });
    }

    function removeDropdown() {
        if (currentDropdown) {
            currentDropdown.remove();
            currentDropdown = null;
        }
    }
}

// ============================================================
// 产品分组：超过 3 篇文档时展开 / 收起
// ============================================================
function initGroupToggle() {
    document.querySelectorAll('.group-toggle-btn').forEach(function (btn) {
        btn.addEventListener('click', function () {
            const card = btn.closest('.product-group-card');
            if (!card) return;
            const expanded = btn.dataset.expanded === 'true';
            card.querySelectorAll('.extra-doc').forEach(function (row) {
                row.hidden = expanded;
            });
            const total = card.querySelectorAll('.product-doc-row').length;
            btn.textContent = expanded ? ('展开全部 ' + total + ' 篇 ▾') : '收起 ▴';
            btn.dataset.expanded = expanded ? 'false' : 'true';
        });
    });
}

// ============================================================
// 收藏 / 取消收藏（AJAX）
// ============================================================
function initFavoriteButtons() {
    document.querySelectorAll('.btn-favorite').forEach(function (btn) {
        btn.addEventListener('click', function () {
            const docId = btn.dataset.docId;
            const isFav = btn.dataset.favorited === 'true';
            const url = isFav ? '/api/favorite/remove' : '/api/favorite/add';
            const token = getCsrfToken();

            fetch(url, {
                method: 'POST',
                headers: {'Content-Type': 'application/json', 'X-CSRF-Token': token},
                body: JSON.stringify({document_id: docId}),
                credentials: 'same-origin'
            })
            .then(function (r) {
                if (r.status === 401 || (r.redirected && r.url.includes('/auth/login'))) {
                    window.location.href = '/auth/login?next=' + encodeURIComponent(window.location.pathname);
                    return null;
                }
                return r.json();
            })
            .then(function (data) {
                if (data && data.success) {
                    btn.dataset.favorited = isFav ? 'false' : 'true';
                    btn.classList.toggle('favorited', !isFav);
                    btn.innerHTML = isFav ? '☆ 收藏' : '★ 已收藏';
                }
            })
            .catch(function () { alert('操作失败，请稍后重试'); });
        });
    });
}

// ============================================================
// 阅读进度自动保存（仅文档阅读页）
// ============================================================
function initReadingProgress() {
    const bar = document.querySelector('.reading-progress-fill');
    if (!bar) return;
    const docId = bar.dataset.docId;
    let saved = 0;
    let timer = null;

    function update() {
        const scrollTop = window.scrollY;
        const docHeight = document.documentElement.scrollHeight - window.innerHeight;
        const progress = docHeight > 0 ? Math.min(1, scrollTop / docHeight) : 0;
        bar.style.width = (progress * 100) + '%';

        // 每前进 10% 保存一次
        if (Math.floor(progress * 10) > saved) {
            saved = Math.floor(progress * 10);
            clearTimeout(timer);
            timer = setTimeout(function () { saveProgress(docId, progress); }, 800);
        }
    }

    window.addEventListener('scroll', update, {passive: true});
    update();
}

function saveProgress(docId, progress) {
    fetch('/api/reading/progress', {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-CSRF-Token': getCsrfToken()},
        body: JSON.stringify({document_id: docId, progress: progress}),
        credentials: 'same-origin'
    }).catch(function () {});
}

// ============================================================
// 回到顶部
// ============================================================
function initBackToTop() {
    const btn = document.createElement('button');
    btn.className = 'back-to-top';
    btn.innerHTML = '↑';
    btn.setAttribute('aria-label', '回到顶部');
    document.body.appendChild(btn);

    window.addEventListener('scroll', function () {
        btn.classList.toggle('show', window.scrollY > 400);
    }, {passive: true});

    btn.addEventListener('click', function () {
        window.scrollTo({top: 0, behavior: 'smooth'});
    });
}

// ============================================================
// Flash 提示自动消失
// ============================================================
function initFlashDismiss() {
    document.querySelectorAll('.flash-message').forEach(function (msg) {
        setTimeout(function () {
            msg.style.opacity = '0';
            setTimeout(function () { msg.remove(); }, 300);
        }, 4000);
    });
}

// ============================================================
// 工具函数
// ============================================================
function getCsrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.content : '';
}

function escapeHtml(str) {
    if (str == null) return '';
    return String(str)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
