/* ============================================
   System Monitor Dashboard — JavaScript
   Live polling, table rendering, stat cards,
   page navigation, devices page
   ============================================ */

(function () {
    'use strict';

    // ---- Config ----
    const POLL_INTERVAL = 300;
    const API_URL = '/api/agents/';

    // ---- State ----
    let latestData = null;
    let selectedDeviceFilter = 'all';

    // ---- DOM Refs: Dashboard ----
    const activeAgentsValue = document.getElementById('active-agents-value');
    const totalStorageValue = document.getElementById('total-storage-value');
    const deviceFilterSelect = document.getElementById('device-filter-select');
    const emptyState = document.getElementById('empty-state');
    const tableWrapper = document.getElementById('table-wrapper');
    const activitiesTbody = document.getElementById('activities-tbody');

    if (deviceFilterSelect) {
        deviceFilterSelect.addEventListener('change', function () {
            selectedDeviceFilter = this.value;
            if (latestData) updateDashboard(latestData);
        });
    }

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

    // ---- DOM Refs: Files Page ----
    const totalFilesValue = document.getElementById('total-files-value');
    const filesStorageValue = document.getElementById('files-storage-value');
    const filesDeviceSelect = document.getElementById('files-device-select');
    const filesDriveSelect = document.getElementById('files-drive-select');
    const driveTabsBar = document.getElementById('drive-tabs-bar');
    const filesSearchInput = document.getElementById('files-search-input');
    const filesEmptyState = document.getElementById('files-empty-state');
    const filesTableWrapper = document.getElementById('files-table-wrapper');
    const filesTbody = document.getElementById('files-tbody');
    const btnRefreshFiles = document.getElementById('btn-refresh-files');

    // ---- DOM Refs: Navigation ----
    const navItems = document.querySelectorAll('.nav-item');
    const pageViews = document.querySelectorAll('.page-view');

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
        'nav-storage': 'page-storage',
        'nav-files': 'page-files',
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
        if (pageId) {
            navigateToPage(pageId);
        }
    });

    function navigateToPage(pageId) {
        const targetPage = document.getElementById(pageId);
        if (!targetPage) return;

        let matchingNavId = null;
        for (const [nId, pId] of Object.entries(pageMap)) {
            if (pId === pageId) {
                matchingNavId = nId;
                break;
            }
        }

        document.querySelectorAll('.nav-item').forEach(n => {
            if (matchingNavId && n.id === matchingNavId) {
                n.classList.add('active');
            } else {
                n.classList.remove('active');
            }
        });

        document.querySelectorAll('.page-view').forEach(p => {
            if (p.id === pageId) {
                p.style.display = 'block';
                p.classList.add('active');
            } else {
                p.style.display = 'none';
                p.classList.remove('active');
            }
        });

        try {
            localStorage.setItem('sm_active_page', pageId);
        } catch (e) {}

        if (latestData) {
            if (pageId === 'page-dashboard') updateDashboard(latestData);
            if (pageId === 'page-devices') updateDevicesPage(latestData);
            if (pageId === 'page-storage') updateStoragePage(latestData);
            if (pageId === 'page-files') updateFilesPage(latestData);
        }
    }

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

        // Populate device filter dropdown with ONLY enrolled devices
        if (deviceFilterSelect) {
            if (agents.length === 0) {
                deviceFilterSelect.innerHTML = '<option value="">No Devices Enrolled</option>';
                selectedDeviceFilter = null;
            } else {
                let optionsHtml = '';
                agents.forEach(agent => {
                    const name = agent.hostname || 'Unknown PC';
                    const user = agent.username ? ` (${agent.username})` : '';
                    optionsHtml += `<option value="${name}">🖥️ ${name}${user}</option>`;
                });

                // Auto-select first device if no selection or selection missing
                if (!selectedDeviceFilter || !agents.some(a => a.hostname === selectedDeviceFilter)) {
                    selectedDeviceFilter = agents[0].hostname;
                }

                if (deviceFilterSelect.innerHTML !== optionsHtml) {
                    deviceFilterSelect.innerHTML = optionsHtml;
                    deviceFilterSelect.value = selectedDeviceFilter;
                } else if (deviceFilterSelect.value !== selectedDeviceFilter) {
                    deviceFilterSelect.value = selectedDeviceFilter;
                }
            }
        }

        // Active Agents stat card: show count like "1 Agent" or "3 Agents"
        const totalAgentCount = agents.length;
        const agentLabel = totalAgentCount === 1 ? '1 Agent' : `${totalAgentCount} Agents`;
        setTextIfChanged(activeAgentsValue, agentLabel);

        let ss = { total: 0, used: 0 };
        const selAgent = agents.find(a => a.hostname === selectedDeviceFilter || a.agent_id === selectedDeviceFilter) || (agents.length > 0 ? agents[0] : null);
        if (selAgent && selAgent.drives && selAgent.drives.length > 0) {
            const t = selAgent.drives.reduce((sum, d) => sum + (d.total || 0), 0);
            const u = selAgent.drives.reduce((sum, d) => sum + (d.used || 0), 0);
            ss = { total: t, used: u, free: Math.max(0, t - u) };
        }
        setTextIfChanged(totalStorageValue, `${formatBytesShort(ss.used)} / ${formatBytesShort(ss.total)}`);

        // 2. Recent PC changes filtered strictly for the selected device
        let activities = data.recent_activities || [];
        if (selAgent) {
            const filterLow = (selAgent.hostname || '').toLowerCase();
            activities = activities.filter(act => {
                const actHost = (act.hostname || '').toLowerCase();
                return actHost === filterLow;
            });
        }

        // If no devices are online or no activities found for selected device
        if (stats.online === 0 || activities.length === 0) {
            if (emptyState) {
                emptyState.style.display = 'flex';
                const emptyHint = emptyState.querySelector('.empty-hint');
                const emptyText = emptyState.querySelector('.empty-text');
                if (stats.online > 0) {
                    if (emptyText) emptyText.textContent = 'No file changes detected yet.';
                    if (emptyHint) emptyHint.innerHTML = 'Monitoring file system in the background.';
                } else {
                    if (emptyText) emptyText.textContent = 'No activity recorded yet.';
                    if (emptyHint) emptyHint.innerHTML = 'Run <code>agent_client.exe</code> on any machine to start monitoring.';
                }
            }
            if (tableWrapper) tableWrapper.style.display = 'none';
            return;
        }

        if (emptyState) emptyState.style.display = 'none';
        if (tableWrapper) tableWrapper.style.display = 'block';

        if (activitiesTbody) {
            const newHtml = activities.map(renderActivityRow).join('');
            if (activitiesTbody.innerHTML !== newHtml) {
                activitiesTbody.innerHTML = newHtml;
            }
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

        const vaultIcon = `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="#8b0000" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="margin-right:8px; vertical-align:middle; flex-shrink:0;">
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
                <td>
                    <span style="color: #8b0000; font-weight: 600; font-size: 0.88rem;">
                        ${pool.c_storage}
                    </span>
                </td>
                <td>
                    <span style="color: #8b0000; font-weight: 600; font-size: 0.88rem;">
                        ${pool.d_storage}
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

            if (drives.length > 0) {
                // Dynamically find C: partition
                const cDrive = drives.find(d => (d.mountpoint || d.device || '').toUpperCase().includes('C')) || drives[0];
                // Dynamically find D: partition (or second partition)
                const dDrive = drives.find(d => (d.mountpoint || d.device || '').toUpperCase().includes('D')) || (drives.length > 1 ? drives[1] : null);

                const cStorage = cDrive ? `${formatBytesShort(cDrive.used)} / ${formatBytesShort(cDrive.total)}` : '—';
                const dStorage = dDrive ? `${formatBytesShort(dDrive.used)} / ${formatBytesShort(dDrive.total)}` : '—';

                const total = drives.reduce((sum, d) => sum + (d.total || 0), 0);
                const used = drives.reduce((sum, d) => sum + (d.used || 0), 0);
                const percent = total > 0 ? (used / total) * 100 : 0;

                pools.push({
                    pool_name: `${host}${user}`,
                    c_storage: cStorage,
                    d_storage: dStorage,
                    used: used,
                    total: total,
                    percent: percent
                });
            }
        });

        if (pools.length === 0) {
            if (storageEmptyState) storageEmptyState.style.display = 'flex';
            if (storageTableWrapper) storageTableWrapper.style.display = 'none';
            return;
        }

        if (storageEmptyState) storageEmptyState.style.display = 'none';
        if (storageTableWrapper) storageTableWrapper.style.display = 'block';

        if (storageTbody) {
            const newHtml = pools.map(renderStoragePoolRow).join('');
            if (storageTbody.innerHTML !== newHtml) {
                storageTbody.innerHTML = newHtml;
            }
        }
    }

    // ============ FILES PAGE ============

    let selectedFilesDevice = '';
    let selectedFilesDrive = '';
    let filesSearchQuery = '';

    try {
        selectedFilesDevice = localStorage.getItem('sm_files_device') || '';
        selectedFilesDrive = localStorage.getItem('sm_files_drive') || '';
    } catch (e) {}

    if (filesDeviceSelect) {
        filesDeviceSelect.addEventListener('change', function () {
            selectedFilesDevice = this.value;
            selectedFilesDrive = ''; // Reset drive selection to first drive of new device
            try {
                localStorage.setItem('sm_files_device', selectedFilesDevice);
                localStorage.setItem('sm_files_drive', '');
            } catch (e) {}
            if (latestData) updateFilesPage(latestData);
        });
    }

    if (filesDriveSelect) {
        filesDriveSelect.addEventListener('change', function () {
            selectedFilesDrive = this.value;
            try {
                localStorage.setItem('sm_files_drive', selectedFilesDrive);
            } catch (e) {}
            if (latestData) updateFilesPage(latestData);
        });
    }

    if (filesSearchInput) {
        let searchTimeout;
        filesSearchInput.addEventListener('input', function () {
            clearTimeout(searchTimeout);
            searchTimeout = setTimeout(() => {
                filesSearchQuery = this.value.trim();
                if (latestData) updateFilesPage(latestData);
            }, 300);
        });
    }

    if (btnRefreshFiles) {
        btnRefreshFiles.addEventListener('click', function (e) {
            e.preventDefault();
            if (latestData) updateFilesPage(latestData);
        });
    }

    function getFileIcon(ext) {
        ext = (ext || '').toLowerCase();
        if (['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.svg'].includes(ext)) return '📷';
        if (['.pdf'].includes(ext)) return '📄';
        if (['.doc', '.docx', '.txt', '.rtf', '.odt'].includes(ext)) return '📝';
        if (['.xls', '.xlsx', '.csv'].includes(ext)) return '📊';
        if (['.zip', '.rar', '.7z', '.tar', '.gz'].includes(ext)) return '🗜️';
        if (['.mp4', '.avi', '.mov', '.mkv'].includes(ext)) return '🎬';
        if (['.mp3', '.wav', '.flac'].includes(ext)) return '🎵';
        return '📁';
    }

    function renderFileRow(f) {
        const isDeleted = f.is_deleted_on_client;
        const badge = isDeleted
            ? `<span class="badge-deleted-pc">⚠️ Deleted from PC (Cloud Intact)</span>`
            : `<span class="badge-active-pc">● Active on PC</span>`;

        return `
            <tr>
                <td>
                    <div style="display:flex; align-items:center;">
                        <span class="file-icon-badge">${getFileIcon(f.file_extension)}</span>
                        <div>
                            <strong style="color: var(--text-primary); font-size: 0.88rem;">${f.file_name}</strong>
                            <div style="font-size: 0.75rem; color: var(--text-muted);">${f.file_extension ? f.file_extension.toUpperCase() + ' File' : 'File'}</div>
                        </div>
                    </div>
                </td>
                <td><span style="font-weight:600; color: var(--text-primary);">${f.hostname}</span></td>
                <td><code style="font-size: 0.78rem; background: rgba(0,0,0,0.04); padding: 2px 6px; border-radius: 4px; color: var(--accent);">${f.file_path}</code></td>
                <td><strong style="color: var(--text-primary);">${formatBytes(f.file_size)}</strong></td>
                <td>${badge}</td>
                <td style="color: var(--text-muted); font-size: 0.82rem;">${timeAgo(f.uploaded_at)}</td>
                <td>
                    <div style="display:flex; align-items:center; gap:6px;">
                        <a href="/api/files/download/${f.id}/?mode=view" class="btn-view-file" data-file-id="${f.id}" data-file-name="${f.file_name}" data-file-ext="${f.file_extension || ''}" data-file-size="${formatBytes(f.file_size)}" target="_blank" title="View / Preview File">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:13px;height:13px;">
                                <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path>
                                <circle cx="12" cy="12" r="3"></circle>
                            </svg>
                            View
                        </a>
                        <a href="/api/files/download/${f.id}/" class="btn-download-file" target="_blank" download title="Download File">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:13px;height:13px;">
                                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                                <polyline points="7 10 12 15 17 10"></polyline>
                                <line x1="12" y1="15" x2="12" y2="3"></line>
                            </svg>
                            Download
                        </a>
                    </div>
                </td>
            </tr>
        `;
    }

    async function updateFilesPage(data) {
        const agents = (data && data.agents) ? data.agents : [];

        // Auto-select first device if none selected or selection invalid
        const validHostnames = agents.map(a => a.hostname).filter(Boolean);
        if ((!selectedFilesDevice || !validHostnames.includes(selectedFilesDevice)) && validHostnames.length > 0) {
            selectedFilesDevice = validHostnames[0];
        }

        // Update filesDeviceSelect options (Only genuine devices, no 'All Devices')
        if (filesDeviceSelect) {
            let optionsHtml = '';
            if (validHostnames.length === 0) {
                optionsHtml = '<option value="" disabled selected>No Devices Connected</option>';
            } else {
                agents.forEach(a => {
                    const h = a.hostname || 'Unknown';
                    const isSel = (h === selectedFilesDevice) ? 'selected' : '';
                    optionsHtml += `<option value="${h}" ${isSel}>💻 ${h}</option>`;
                });
            }
            if (filesDeviceSelect.innerHTML !== optionsHtml) {
                filesDeviceSelect.innerHTML = optionsHtml;
            }
            if (selectedFilesDevice) {
                filesDeviceSelect.value = selectedFilesDevice;
            }
        }

        // Discover active non-C drives from the currently selected agent
        const availableDrives = new Set();
        const activeAgent = agents.find(a => a.hostname === selectedFilesDevice) || agents[0];
        if (activeAgent && activeAgent.drives) {
            activeAgent.drives.forEach(d => {
                const letter = (d.device || d.mountpoint || '').toUpperCase().split(':')[0];
                if (letter && letter !== 'C' && letter.length === 1 && letter >= 'A' && letter <= 'Z') {
                    availableDrives.add(`${letter}:`);
                }
            });
        }

        const sortedDrives = Array.from(availableDrives).sort();

        // If no secondary drives on this device
        if (sortedDrives.length === 0) {
            selectedFilesDrive = '';
            if (filesDriveSelect) {
                filesDriveSelect.innerHTML = '<option value="" disabled selected>No Secondary Drives</option>';
            }
            if (filesEmptyState) {
                filesEmptyState.style.display = 'flex';
                const emptyText = filesEmptyState.querySelector('.empty-text');
                const emptyHint = filesEmptyState.querySelector('.empty-hint');
                if (emptyText) emptyText.textContent = 'No secondary drive found on this device.';
                if (emptyHint) emptyHint.textContent = 'This machine only has a primary C: drive partition.';
            }
            if (filesTableWrapper) filesTableWrapper.style.display = 'none';
            setTextIfChanged(totalFilesValue, '0 Files');
            setTextIfChanged(filesStorageValue, '0 B');
            return;
        }

        if (!selectedFilesDrive || !sortedDrives.includes(selectedFilesDrive)) {
            selectedFilesDrive = sortedDrives[0];
        }

        // Update filesDriveSelect options (100% dynamic, only genuine drives reported by the device)
        if (filesDriveSelect) {
            let driveOptionsHtml = '';
            sortedDrives.forEach(drv => {
                const isSel = (drv === selectedFilesDrive) ? 'selected' : '';
                driveOptionsHtml += `<option value="${drv}" ${isSel}>💾 ${drv} Drive</option>`;
            });
            if (filesDriveSelect.innerHTML !== driveOptionsHtml) {
                filesDriveSelect.innerHTML = driveOptionsHtml;
            }
            filesDriveSelect.value = selectedFilesDrive;
        }

        // Fetch files from API for the selected device and drive
        try {
            let url = `/api/files/?hostname=${encodeURIComponent(selectedFilesDevice)}&drive=${encodeURIComponent(selectedFilesDrive)}`;
            if (filesSearchQuery) {
                url += `&search=${encodeURIComponent(filesSearchQuery)}`;
            }

            const res = await fetch(url);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const filesRes = await res.json();
            const files = filesRes.files || [];

            // Compute total size
            const totalBytes = files.reduce((sum, f) => sum + (f.file_size || 0), 0);

            setTextIfChanged(totalFilesValue, `${files.length} File${files.length !== 1 ? 's' : ''}`);
            setTextIfChanged(filesStorageValue, formatBytes(totalBytes));

            if (files.length === 0) {
                if (filesEmptyState) filesEmptyState.style.display = 'flex';
                if (filesTableWrapper) filesTableWrapper.style.display = 'none';
                return;
            }

            if (filesEmptyState) filesEmptyState.style.display = 'none';
            if (filesTableWrapper) filesTableWrapper.style.display = 'block';

            if (filesTbody) {
                const newHtml = files.map(renderFileRow).join('');
                if (filesTbody.innerHTML !== newHtml) {
                    filesTbody.innerHTML = newHtml;
                }
            }
        } catch (err) {
            console.error('Failed to load files:', err);
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
                if (activePage.id === 'page-files') updateFilesPage(data);
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

    // ============ FILE PREVIEW MODAL CONTROLLER ============
    const previewModal = document.getElementById('file-preview-modal');
    const previewCloseBtn = document.getElementById('preview-close-btn');
    const previewFileName = document.getElementById('preview-file-name');
    const previewFileMeta = document.getElementById('preview-file-meta');
    const previewFileIcon = document.getElementById('preview-file-icon');
    const previewModalBody = document.getElementById('preview-modal-body');
    const previewOpenTabBtn = document.getElementById('preview-open-tab-btn');
    const previewDownloadBtn = document.getElementById('preview-download-btn');

    function closeFilePreview() {
        if (previewModal) {
            previewModal.style.display = 'none';
            if (previewModalBody) previewModalBody.innerHTML = '<div class="preview-loading">Loading file preview...</div>';
        }
    }

    if (previewModal) {
        if (previewCloseBtn) {
            previewCloseBtn.addEventListener('click', closeFilePreview);
        }

        previewModal.addEventListener('click', function (e) {
            if (e.target === previewModal) {
                closeFilePreview();
            }
        });

        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && previewModal.style.display === 'flex') {
                closeFilePreview();
            }
        });
    }

    // Event delegation for View buttons in files table
    document.addEventListener('click', function (e) {
        const viewBtn = e.target.closest('.btn-view-file');
        if (!viewBtn) return;

        e.preventDefault();
        const fileId = viewBtn.dataset.fileId;
        const fileName = viewBtn.dataset.fileName || 'File';
        const fileExt = (viewBtn.dataset.fileExt || '').toLowerCase();
        const fileSize = viewBtn.dataset.fileSize || '';

        if (!fileId || !previewModal) return;

        const viewUrl = `/api/files/download/${fileId}/?mode=view`;
        const downloadUrl = `/api/files/download/${fileId}/`;

        if (previewFileName) previewFileName.textContent = fileName;
        if (previewFileMeta) previewFileMeta.textContent = `${fileExt ? fileExt.toUpperCase() + ' • ' : ''}${fileSize}`;
        if (previewFileIcon) previewFileIcon.textContent = getFileIcon(fileExt);
        if (previewOpenTabBtn) previewOpenTabBtn.href = viewUrl;
        if (previewDownloadBtn) previewDownloadBtn.href = downloadUrl;

        previewModal.style.display = 'flex';
        previewModalBody.innerHTML = '<div class="preview-loading">Loading file preview...</div>';

        const imageExts = ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg', '.bmp'];
        const textExts = ['.txt', '.py', '.js', '.html', '.css', '.json', '.md', '.csv', '.sql', '.sh', '.bat', '.ps1', '.ts', '.c', '.cpp', '.h', '.java'];

        if (imageExts.includes(fileExt)) {
            previewModalBody.innerHTML = `<img src="${viewUrl}" alt="${fileName}" style="max-width:100%; max-height:70vh; object-fit:contain;" />`;
        } else if (fileExt === '.pdf') {
            previewModalBody.innerHTML = `<iframe src="${viewUrl}" title="${fileName}" style="width:100%; height:70vh; border:none;"></iframe>`;
        } else if (textExts.includes(fileExt)) {
            fetch(viewUrl)
                .then(r => r.text())
                .then(text => {
                    const escaped = text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
                    previewModalBody.innerHTML = `<pre>${escaped}</pre>`;
                })
                .catch(err => {
                    previewModalBody.innerHTML = `<div style="text-align:center; color:#EF4444;"><p>Unable to load text preview.</p><a href="${viewUrl}" target="_blank" class="btn-preview-top" style="margin-top:10px;">Open in New Tab</a></div>`;
                });
        } else {
            previewModalBody.innerHTML = `
                <div style="text-align:center; padding: 30px 20px;">
                    <div style="font-size: 3rem; margin-bottom: 12px;">${getFileIcon(fileExt)}</div>
                    <h4 style="margin: 0 0 6px 0; color: #111827; font-size: 1.05rem;">${fileName}</h4>
                    <p style="color: #6B7280; font-size: 0.85rem; margin-bottom: 18px;">Direct inline preview is not supported for this file type.</p>
                    <div style="display:flex; justify-content:center; gap:10px;">
                        <a href="${viewUrl}" target="_blank" class="btn-preview-top">Open in Browser</a>
                        <a href="${downloadUrl}" class="btn-preview-top btn-preview-download" download>Download File</a>
                    </div>
                </div>
            `;
        }
    });

    // Restore saved active page on page refresh
    try {
        const savedPage = localStorage.getItem('sm_active_page');
        if (savedPage && savedPage !== 'page-dashboard') {
            navigateToPage(savedPage);
        }
    } catch (e) {}

    // Initial fetch
    fetchAgents();

    // Start polling every 500ms
    setInterval(fetchAgents, POLL_INTERVAL);

})();
