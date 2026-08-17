/* ============================================
   System Monitor Dashboard — JavaScript
   Live polling, table rendering, stat cards,
   page navigation, devices page
   ============================================ */

(function () {
    'use strict';

    // ---- Config ----
    const POLL_INTERVAL = 500;
    const API_URL = '/api/agents/';

    // ---- DOM Refs: Dashboard ----
    const activeAgentsValue = document.getElementById('active-agents-value');
    const totalStorageValue = document.getElementById('total-storage-value');
    const dashboardEnrolledValue = document.getElementById('dashboard-enrolled-value');
    const emptyState = document.getElementById('empty-state');
    const tableWrapper = document.getElementById('table-wrapper');
    const activitiesTbody = document.getElementById('activities-tbody');


    // ---- DOM Refs: Devices Page ----
    const totalEndpointsValue = document.getElementById('total-endpoints-value');
    const activeSyncingValue = document.getElementById('active-syncing-value');
    const offlineValue = document.getElementById('offline-value');
    const devicesEmptyState = document.getElementById('devices-empty-state');
    const devicesTableWrapper = document.getElementById('devices-table-wrapper');
    const devicesTbody = document.getElementById('devices-tbody');

    // ---- DOM Refs: Storage Page ----
    const storageEmptyState = document.getElementById('storage-empty-state');
    const storageTableWrapper = document.getElementById('storage-table-wrapper');
    const storageTbody = document.getElementById('storage-tbody');

    // ---- DOM Refs: Navigation ----
    const navItems = document.querySelectorAll('.nav-item');
    const pageViews = document.querySelectorAll('.page-view');

    // ---- State ----
    let latestData = null;

    // ---- Helpers ----
    function formatBytes(bytes) {
        if (bytes == null || bytes === 0) return '0 B';
        const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
        const i = Math.floor(Math.log(bytes) / Math.log(1024));
        return (bytes / Math.pow(1024, i)).toFixed(1) + ' ' + sizes[i];
    }

    function formatBytesShort(bytes) {
        if (bytes == null || bytes === 0) return '0 GB';
        const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
        const i = Math.floor(Math.log(bytes) / Math.log(1024));
        const val = bytes / Math.pow(1024, i);
        return val.toFixed(val >= 100 ? 0 : 1) + ' ' + sizes[i];
    }

    function timeAgo(isoString) {
        if (!isoString) return '—';
        const now = new Date();
        const then = new Date(isoString);
        const diffMs = now - then;
        const diffSec = Math.floor(diffMs / 1000);
        if (diffSec < 5) return 'Just now';
        if (diffSec < 60) return diffSec + ' secs ago';
        const diffMin = Math.floor(diffSec / 60);
        if (diffMin === 1) return '1 min ago';
        if (diffMin < 60) return diffMin + ' mins ago';
        const diffHr = Math.floor(diffMin / 60);
        if (diffHr === 1) return '1 hour ago';
        return diffHr + ' hours ago';
    }

    function getProgressClass(percent) {
        return percent > 85 ? 'high' : '';
    }

    // Get total vault space (sum of all drives) for a single agent
    function getAgentVaultSpace(agent) {
        if (!agent.drives || agent.drives.length === 0) return 0;
        return agent.drives.reduce((sum, d) => sum + (d.total || 0), 0);
    }

    function getDashboardVaultStorage(data) {
        if (data && data.vault_storage && data.vault_storage.total > 0) {
            return data.vault_storage;
        }

        // Aggregate genuine storage across all enrolled agents
        if (data && data.agents && data.agents.length > 0) {
            let total = 0;
            let used = 0;
            for (const agent of data.agents) {
                if (agent.drives && agent.drives.length > 0) {
                    total += agent.drives.reduce((sum, d) => sum + (d.total || 0), 0);
                    used += agent.drives.reduce((sum, d) => sum + (d.used || 0), 0);
                }
            }
            if (total > 0) {
                return { total, used, free: Math.max(0, total - used) };
            }
        }

        return { total: 0, used: 0 };
    }

    // Get primary drive path for backup target
    function getBackupPath(agent) {
        if (!agent.drives || agent.drives.length === 0) return '—';
        const primary = agent.drives[0];
        const user = agent.username || 'user';
        if (primary.mountpoint && primary.mountpoint.includes('C')) {
            return `C:\\Users\\${user}\\Documents`;
        }
        return primary.mountpoint || '—';
    }

    // Determine OS short name
    function getOsShort(osInfo) {
        if (!osInfo) return 'Unknown';
        if (osInfo.toLowerCase().includes('windows 11')) return 'Windows 11 Pro';
        if (osInfo.toLowerCase().includes('windows 10')) return 'Windows 10 Pro';
        if (osInfo.toLowerCase().includes('windows')) return 'Windows';
        if (osInfo.toLowerCase().includes('darwin') || osInfo.toLowerCase().includes('mac')) return 'macOS';
        if (osInfo.toLowerCase().includes('linux')) return 'Linux';
        return osInfo.split('(')[0].trim();
    }

    // ---- Compute aggregate stats ----
    function computeStats(agents) {
        const online = agents.filter(a => a.is_online).length;
        const offline = agents.length - online;

        const onlineAgents = agents.filter(a => a.is_online && a.cpu_usage != null);
        let avgCpu = 0;
        if (onlineAgents.length > 0) {
            avgCpu = onlineAgents.reduce((sum, a) => sum + a.cpu_usage, 0) / onlineAgents.length;
        }

        return { online, offline, avgCpu, total: agents.length };
    }

    // ============ NAVIGATION ============

    // Map nav IDs to page IDs
    const pageMap = {
        'nav-dashboard': 'page-dashboard',
        'nav-devices': 'page-devices',
        'nav-backup-jobs': 'page-backup-jobs',
        'nav-storage': 'page-storage',
        'nav-recovery': 'page-recovery',
        'nav-security': 'page-security',
        'nav-logs': 'page-logs',
        'nav-settings': 'page-settings',
    };

    document.addEventListener('click', function (e) {
        const syncBtn = e.target.closest('.btn-sync, .btn-run-now');
        if (syncBtn) {
            e.preventDefault();
            const row = syncBtn.closest('tr');
            let jobName = 'Manual Backup';
            if (row) {
                const nameEl = row.querySelector('.device-name');
                if (nameEl) jobName = nameEl.textContent.trim();
            }

            syncBtn.disabled = true;
            syncBtn.textContent = 'Syncing...';

            fetch('/api/activities/create/', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    job_name: jobName,
                    data_size: '0 KB',
                    status: 'Success',
                    status_type: 'success'
                })
            }).then(() => {
                syncBtn.disabled = false;
                syncBtn.textContent = syncBtn.classList.contains('btn-run-now') ? 'Run Now' : 'Sync';
                fetchAgents();
            }).catch(err => {
                console.error('Failed to trigger backup:', err);
                syncBtn.disabled = false;
                syncBtn.textContent = 'Failed';
            });
            return;
        }

        const deleteBtn = e.target.closest('.btn-delete-agent');
        if (deleteBtn) {
            e.preventDefault();
            const agentId = deleteBtn.getAttribute('data-agent-id');
            const userName = deleteBtn.getAttribute('data-username') || 'this user';

            if (confirm(`Are you sure you want to delete user ${userName}?`)) {
                deleteBtn.disabled = true;
                deleteBtn.textContent = 'Deleting...';

                fetch(`/api/agents/delete/${agentId}/`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' }
                })
                .then(r => r.json())
                .then(data => {
                    fetchAgents();
                })
                .catch(err => {
                    console.error('Failed to delete agent:', err);
                    deleteBtn.disabled = false;
                    deleteBtn.textContent = 'Delete';
                });
            }
            return;
        }

        const navItem = e.target.closest('.nav-item');
        if (!navItem) return;

        e.preventDefault();

        const pageId = pageMap[navItem.id];
        if (!pageId) return;

        const targetPage = document.getElementById(pageId);
        if (!targetPage) return;

        // Update active nav item
        document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
        navItem.classList.add('active');

        // Hide ALL page views
        document.querySelectorAll('.page-view').forEach(p => {
            p.classList.remove('active');
            p.style.display = 'none';
        });

        // Show target page
        targetPage.style.display = 'block';
        targetPage.classList.add('active');

        // Re-render with latest data
        if (latestData) {
            if (pageId === 'page-dashboard') updateDashboard(latestData);
            if (pageId === 'page-devices') updateDevicesPage(latestData);
            if (pageId === 'page-storage') updateStoragePage(latestData);
        }
    });

    // ---- Render Recent Activity Row ----
    function renderActivityRow(act) {
        const isSuccess = act.status_type === 'success';
        const badgeClass = isSuccess ? 'running-job' : 'active-job';
        const badgeText = isSuccess ? '✓ Success' : '! Failed';

        return `
            <tr>
                <td><strong style="color: var(--text-primary);">${act.event}</strong></td>
                <td style="color: var(--text-muted);">${act.time}</td>
                <td><span style="color: var(--accent); font-weight: 600; font-size: 0.85rem;">${act.data_size}</span></td>
                <td>
                    <span class="status-badge ${badgeClass}">
                        ${badgeText}
                    </span>
                </td>
            </tr>
        `;
    }

    // Only update DOM text if value has actually changed (prevents flicker)
    function setTextIfChanged(el, newText) {
        if (el && el.textContent !== newText) {
            el.textContent = newText;
        }
    }

    function updateDashboard(data) {
        const agents = data.agents || [];
        const stats = computeStats(agents);

        setTextIfChanged(activeAgentsValue, `${stats.online} Online`);

        const ss = getDashboardVaultStorage(data);
        setTextIfChanged(totalStorageValue, `${formatBytesShort(ss.used)} / ${formatBytesShort(ss.total)}`);
        setTextIfChanged(dashboardEnrolledValue, `${stats.total} Device${stats.total !== 1 ? 's' : ''}`);

        const activities = data.recent_activities || [];

        // If no devices are online, show empty state regardless of old activities
        if (stats.online === 0 || activities.length === 0) {
            if (emptyState) emptyState.style.display = 'flex';
            if (tableWrapper) tableWrapper.style.display = 'none';
            return;
        }

        if (emptyState) emptyState.style.display = 'none';
        if (tableWrapper) tableWrapper.style.display = 'block';

        if (activitiesTbody) {
            activitiesTbody.innerHTML = activities.map(renderActivityRow).join('');
        }
    }

    // ============ DEVICES PAGE ============

    function renderDeviceRow(agent) {
        const statusClass = agent.is_online ? 'online' : 'offline';
        const statusText = agent.is_online ? 'Online' : 'Offline';
        const vaultSpace = getAgentVaultSpace(agent);
        const backupPath = getBackupPath(agent);
        const osShort = getOsShort(agent.os_info);

        const monitorIcon = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <rect x="2" y="3" width="20" height="14" rx="2" ry="2"/>
            <line x1="8" y1="21" x2="16" y2="21"/>
            <line x1="12" y1="17" x2="12" y2="21"/>
        </svg>`;

        return `
            <tr>
                <td>
                    <div class="device-cell">
                        <div class="device-icon">${monitorIcon}</div>
                        <div class="device-name-wrap">
                            <span class="device-name">${agent.hostname || 'Unknown'}</span>
                            <span class="device-os">${osShort}</span>
                        </div>
                    </div>
                </td>
                <td>${agent.local_ip || agent.public_ip || '—'}</td>
                <td>${agent.username || '—'}</td>
                <td><span style="font-family: monospace; font-weight: 400; color: var(--text-secondary); font-size: 0.85rem; letter-spacing: 0.03em;">${agent.mac_address || '—'}</span></td>
                <td><span class="vault-space">${formatBytesShort(vaultSpace)}</span></td>
                <td>
                    <span class="status-badge ${statusClass}">
                        <span class="status-badge-dot"></span>
                        ${statusText}
                    </span>
                </td>
                <td>
                    <button class="btn-delete-agent" data-agent-id="${agent.agent_id}" data-username="${agent.username || agent.hostname || 'User'}" title="Delete User">
                        <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="margin-right:4px;">
                            <polyline points="3 6 5 6 21 6"></polyline>
                            <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
                        </svg>
                        Delete
                    </button>
                </td>
            </tr>
        `;
    }

    function updateDevicesPage(data) {
        const agents = data.agents || [];
        const stats = computeStats(agents);

        // Update stat cards (only if value changed — prevents flicker)
        setTextIfChanged(totalEndpointsValue, `${stats.total} Device${stats.total !== 1 ? 's' : ''}`);
        const totalEndpointsCard = document.getElementById('card-total-endpoints');
        if (stats.total === 0) {
            totalEndpointsCard.style.display = 'none';
        } else {
            totalEndpointsCard.style.display = 'block';
        }
        setTextIfChanged(activeSyncingValue, `${stats.online} Device${stats.online !== 1 ? 's' : ''}`);
        setTextIfChanged(offlineValue, `${stats.offline} Device${stats.offline !== 1 ? 's' : ''}`);

        // Toggle empty / table
        if (agents.length === 0) {
            devicesEmptyState.style.display = 'flex';
            devicesTableWrapper.style.display = 'none';
            return;
        }

        devicesEmptyState.style.display = 'none';
        devicesTableWrapper.style.display = 'block';

        // Sort deterministically: newest enrolled device always appears on top
        agents.sort((a, b) => {
            const timeA = a.first_seen ? new Date(a.first_seen).getTime() : 0;
            const timeB = b.first_seen ? new Date(b.first_seen).getTime() : 0;
            if (timeB !== timeA) return timeB - timeA;
            return (b.hostname || '').localeCompare(a.hostname || '');
        });

        const newTbodyHtml = agents.map(renderDeviceRow).join('');
        if (devicesTbody.innerHTML !== newTbodyHtml) {
            devicesTbody.innerHTML = newTbodyHtml;
        }
    }

    // ============ STORAGE PAGE ============

    function renderStoragePoolRow(pool) {
        const isOptimal = pool.percent < 85;
        const statusText = isOptimal ? '• Optimal' : '• Low Storage';

        const vaultIcon = `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="#8b0000" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="margin-right:8px; vertical-align:middle;">
            <rect x="2" y="4" width="20" height="8" rx="2"/>
            <rect x="2" y="14" width="20" height="8" rx="2"/>
            <line x1="6" y1="8" x2="6.01" y2="8"/>
            <line x1="6" y1="18" x2="6.01" y2="18"/>
        </svg>`;

        const badgeBg = isOptimal ? 'rgba(16, 185, 129, 0.1)' : 'rgba(239, 68, 68, 0.1)';
        const badgeColor = isOptimal ? '#10b981' : '#ef4444';
        const badgeBorder = isOptimal ? 'rgba(16, 185, 129, 0.2)' : 'rgba(239, 68, 68, 0.2)';

        return `
            <tr>
                <td>
                    <div style="display:flex; align-items:center;">
                        ${vaultIcon}
                        <strong style="color: var(--text-primary); font-size: 0.9rem;">${pool.pool_name}</strong>
                    </div>
                </td>
                <td><code style="font-family: monospace; color: var(--text-secondary); font-size: 0.85rem;">${pool.mount_dir}</code></td>
                <td>
                    <span style="color: #8b0000; font-weight: 600; font-size: 0.88rem;">
                        ${formatBytesShort(pool.used)} / ${formatBytesShort(pool.total)}
                    </span>
                </td>
                <td>
                    <span class="status-badge" style="background: ${badgeBg}; color: ${badgeColor}; border: 1px solid ${badgeBorder}; padding: 3px 10px; border-radius: 100px; font-weight: 600;">
                        ${statusText}
                    </span>
                </td>
            </tr>
        `;
    }

    function updateStoragePage(data) {
        const agents = data.agents || [];
        const pools = [];

        agents.forEach(agent => {
            const host = agent.hostname || 'Device';
            const user = agent.username ? ` (${agent.username})` : '';
            const drives = agent.drives || [];

            drives.forEach(d => {
                const driveLetter = d.mountpoint || d.device || 'Storage Drive';
                pools.push({
                    pool_name: `${host}${user} — Drive ${driveLetter}`,
                    mount_dir: driveLetter,
                    used: d.used || 0,
                    total: d.total || 0,
                    percent: d.percent || 0
                });
            });
        });

        if (pools.length === 0) {
            if (storageEmptyState) storageEmptyState.style.display = 'flex';
            if (storageTableWrapper) storageTableWrapper.style.display = 'none';
            return;
        }

        if (storageEmptyState) storageEmptyState.style.display = 'none';
        if (storageTableWrapper) storageTableWrapper.style.display = 'block';

        if (storageTbody) {
            storageTbody.innerHTML = pools.map(renderStoragePoolRow).join('');
        }
    }

    // ============ POLLING ============

    async function fetchAgents() {
        try {
            const res = await fetch(API_URL);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();
            latestData = data;

            // Update whichever page is currently active
            const activePage = document.querySelector('.page-view.active');
            if (activePage) {
                if (activePage.id === 'page-dashboard') updateDashboard(data);
                if (activePage.id === 'page-devices') updateDevicesPage(data);
                if (activePage.id === 'page-storage') updateStoragePage(data);
            } else {
                updateDashboard(data);
            }
        } catch (err) {
            console.error('Failed to fetch agents:', err);
        }
    }

    // ============ CLEAR RECENT ACTIVITIES MODAL ============
    const clearBtn = document.getElementById('btn-clear-history');
    const modalBackdrop = document.getElementById('clear-modal-backdrop');
    const modalCancelBtn = document.getElementById('btn-modal-cancel');
    const modalConfirmBtn = document.getElementById('btn-modal-confirm');

    if (clearBtn && modalBackdrop) {
        clearBtn.addEventListener('click', function (e) {
            e.preventDefault();
            modalBackdrop.style.display = 'flex';
        });

        if (modalCancelBtn) {
            modalCancelBtn.addEventListener('click', function () {
                modalBackdrop.style.display = 'none';
            });
        }

        modalBackdrop.addEventListener('click', function (e) {
            if (e.target === modalBackdrop) {
                modalBackdrop.style.display = 'none';
            }
        });

        if (modalConfirmBtn) {
            modalConfirmBtn.addEventListener('click', function () {
                modalConfirmBtn.disabled = true;
                modalConfirmBtn.textContent = 'Clearing...';

                fetch('/api/activities/clear/', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' }
                })
                .then(r => r.json())
                .then(data => {
                    modalBackdrop.style.display = 'none';
                    modalConfirmBtn.disabled = false;
                    modalConfirmBtn.textContent = 'Yes, Clear All';
                    fetchAgents();
                })
                .catch(err => {
                    console.error('Failed to clear activities:', err);
                    modalConfirmBtn.disabled = false;
                    modalConfirmBtn.textContent = 'Yes, Clear All';
                });
            });
        }
    }

    // Initial fetch
    fetchAgents();

    // Start polling every 500ms
    setInterval(fetchAgents, POLL_INTERVAL);

})();
