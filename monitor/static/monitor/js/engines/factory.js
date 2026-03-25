/**
 * Digital Twin Factory Engine
 * V2.2.3 [Modularized]
 */

window.FactoryApp = {
    // ══════════════════════════════════════
    // State & Core Vars
    // ══════════════════════════════════════
    state: {
        scene: null,
        camera: null,
        renderer: null,
        controls: null,
        machines: new Map(),
        particles: [],
        currentDevices: [],
        raycaster: new THREE.Raycaster(),
        mouse: new THREE.Vector2(),
        focusId: null,
        clock: new THREE.Clock(),
        ws: null,
        isInitialized: false,
        animationId: null,
        cachedModels: new Map() // 新增：存储加载好的模型模板
    },

    // ══════════════════════════════════════
    // Scene Initiation
    // ══════════════════════════════════════
    // ══════════════════════════════════════
    scene: {
        init: async function () {
            const container = document.getElementById('canvasArea');
            if (!container) return;

            // 1. 初始化模型加载 (V4.2 新增)
            try {
                await FactoryApp.loader.loadAllModels();
            } catch (e) {
                console.error("Critical Model Loading Error:", e);
                // 即使加载失败也允许进入，使用后备模型
            }

            const state = FactoryApp.state;
            state.scene = new THREE.Scene();
            // --- 风格重塑：雅致科技灰 (Slate-800) [V4.6 修正] ---
            state.scene.background = new THREE.Color(0x1e293b);

            // Perspective Camera (更适合沉浸式观察内部设备)
            const aspect = container.clientWidth / container.clientHeight;
            state.camera = new THREE.PerspectiveCamera(45, aspect, 10, 30000);
            state.camera.position.set(0, 1800, 2800); // 居中正斜上方视角
            state.camera.updateProjectionMatrix();

            state.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, logarithmicDepthBuffer: true });
            state.renderer.setPixelRatio(window.devicePixelRatio);
            state.renderer.setSize(container.clientWidth, container.clientHeight);
            state.renderer.shadowMap.enabled = true;
            state.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
            state.renderer.outputEncoding = THREE.sRGBEncoding;

            // --- 提亮方案：色调映射与曝光调整 (V4.4 修正) ---
            state.renderer.toneMapping = THREE.ACESFilmicToneMapping;
            state.renderer.toneMappingExposure = 1.0;

            container.appendChild(state.renderer.domElement);

            state.controls = new THREE.OrbitControls(state.camera, state.renderer.domElement);
            state.controls.enableDamping = true;
            state.controls.dampingFactor = 0.05;
            // --- 风格重塑：自由移动视角 (沉浸式数字孪生) ---
            state.controls.minPolarAngle = 0;              // 允许全方位俯视
            state.controls.maxPolarAngle = Math.PI / 2.1;  // 限制不低过地平线
            state.controls.target.set(0, 0, 0);            // 视点锁定中心
            state.controls.minDistance = 100;              // 允许拉近进入厂房
            state.controls.maxDistance = 6000;             // 允许拉远查看全景
            state.controls.enablePan = true;               // 开启平移功能

            const ambi = new THREE.AmbientLight(0xffffff, 0.6); // 调整：0.8 -> 0.6
            state.scene.add(ambi);

            // --- 提亮方案：新增半球光 (V4.4 修正) ---
            const hemi = new THREE.HemisphereLight(0xffffff, 0x444444, 0.8);
            state.scene.add(hemi);

            const dir = new THREE.DirectionalLight(0xffffff, 1.3); // 调整：1.8 -> 1.3
            dir.position.set(500, 1000, 300);
            dir.castShadow = true;
            dir.shadow.camera.left = -2000; dir.shadow.camera.right = 2000;
            dir.shadow.camera.top = 2000; dir.shadow.camera.bottom = -2000;
            dir.shadow.mapSize.set(2048, 2048);
            state.scene.add(dir);

            FactoryApp.scene.addFactoryScene();
            FactoryApp.scene.createMachineGrid();
            FactoryApp.scene.createDataParticles();

            // Event Listeners
            window.addEventListener('resize', FactoryApp.engine.onResize);
            container.addEventListener('mousemove', FactoryApp.engine.onMouseMove);
            container.addEventListener('click', FactoryApp.engine.onClick);

            FactoryApp.engine.animate();
            FactoryApp.socket.connect();
            FactoryApp.ui.addLog("v4.5 Simulation Active.");

            // 隐藏加载层 (V4.2)
            const loaderOverlay = document.getElementById('loading-overlay');
            if (loaderOverlay) {
                loaderOverlay.style.opacity = '0';
                setTimeout(() => loaderOverlay.style.display = 'none', 500);
            }

            state.isInitialized = true;
        },

        addFactoryScene: function () {
            const state = FactoryApp.state;
            const template = state.cachedModels.get('factory_scene');
            if (template) {
                const factory = template.clone();

                // 初步缩放：根据 5x5 设备阵列 (约1800x1800) 进行适配
                // 暂时预设 50 倍缩放，后续可根据预览结果微调
                const s = 75;
                factory.scale.set(s, s, s);

                // 自动对齐地面
                const box = new THREE.Box3().setFromObject(factory);
                factory.position.y = -box.min.y;

                factory.traverse(node => {
                    if (node.isMesh) {
                        node.receiveShadow = true;
                        node.castShadow = true;
                        // 保持模型完整展现
                        node.visible = true;
                    }
                });

                state.scene.add(factory);
                console.log("Factory Full Scene Integrated. Free Exploration Active.");
            }
        },

        createMachineGrid: function () {
            const spacingZ = 450; // 前后间距
            const spacingX = 650; // 左右车间跨度

            for (let id = 1; id <= 25; id++) {
                let x, z;
                if (id <= 10) {
                    // 第一列 (1-10) 左侧
                    x = -spacingX;
                    z = (id - 5.5) * spacingZ;
                } else if (id <= 20) {
                    // 第二列 (11-20) 中间
                    x = 0;
                    z = (id - 15.5) * spacingZ;
                } else {
                    // 第三列 (21-25) 右侧
                    x = spacingX;
                    z = (id - 23) * spacingZ; // 5台居中排列
                }
                FactoryApp.scene.addMachine(id, x, z);
            }
        },

        addMachine: function (id, x, z) {
            const state = FactoryApp.state;
            const group = new THREE.Group();
            group.position.set(x, 0, z);
            group.userData = { id, name: `Machine-${id.toString().padStart(2, '0')}` };

            // 根据编号范围分配模型
            let modelKey = 'cnc1';
            if (id <= 10) modelKey = 'cnc3';      // 1-10: CNC3
            else if (id <= 20) modelKey = 'cnc2'; // 11-20: CNC2
            else modelKey = 'cnc1';               // 21-25: CNC1

            const template = state.cachedModels.get(modelKey);
            let windowScreen = null;
            let bodyMesh = null;

            if (template) {
                const model = template.clone();

                // --- 个性化比例、旋转与对齐方案 [V5.4 修正] ---
                let s = 80;
                if (modelKey === 'cnc1') {
                    s = 90;
                    model.rotation.x = 0;
                    model.rotation.y = 0;
                } else if (modelKey === 'cnc2') {
                    s = 15;                   // 16 * 0.8 = 12.8
                    model.rotation.y = Math.PI; // 修正前后 (180 deg)
                } else if (modelKey === 'cnc3') {
                    s = 210;                    // 200 * 0.9 = 180
                    model.rotation.y = 0;       // 旋转 180 度 (从原来 PI 翻回到 0)
                }
                model.scale.set(s, s, s);

                group.add(model);

                // 动态高度对齐：自动计算模型包围盒并修正 Position.Y (防止沉入地板)
                const box = new THREE.Box3().setFromObject(model);
                model.position.y = -box.min.y;

                model.traverse(node => {
                    if (node.isMesh) {
                        node.castShadow = true;
                        node.receiveShadow = true;
                        // 寻找模型自带的屏幕部件
                        if (node.name.toLowerCase().includes('screen') || node.name.toLowerCase().includes('glass') || node.name.toLowerCase().includes('monitor')) {
                            windowScreen = node;
                        }
                        if (!bodyMesh) bodyMesh = node;
                    }
                });
                // [已移除] 移除原有的 labelPlate (标签) 和 fallback windowScreen (大蓝板)
            }

            // --- 优化方案：缩小光效覆盖范围 (V4.6) ---
            const glowGeo = new THREE.PlaneGeometry(400, 400);
            const glowMat = new THREE.MeshBasicMaterial({
                map: FactoryApp.utils.createGlowTex(),
                color: 0x409eff,
                transparent: true,
                opacity: 0,
                blending: THREE.AdditiveBlending,
                side: THREE.DoubleSide,
                depthWrite: false
            });
            const glow = new THREE.Mesh(glowGeo, glowMat);
            glow.rotation.x = -Math.PI / 2;
            glow.position.y = 5;
            group.add(glow);

            state.scene.add(group);
            state.machines.set(id, {
                group,
                body: bodyMesh,
                windowScreen,
                labelPlate: null, // 标记为 null
                glow,
                oee: 0.8,
                status: 'Idle',
                phase: Math.random() * Math.PI
            });
        },

        createDataParticles: function () {
            const state = FactoryApp.state;
            const geo = new THREE.BoxGeometry(6, 6, 6);
            for (let i = 0; i < 30; i++) {
                const mat = new THREE.MeshBasicMaterial({ color: 0x409eff, transparent: true });
                const p = new THREE.Mesh(geo, mat);
                FactoryApp.engine.resetParticle(p);
                state.scene.add(p);
                state.particles.push(p);
            }
        }
    },

    // ══════════════════════════════════════
    // Model Loader (V4.2 Add)
    // ══════════════════════════════════════
    loader: {
        loadAllModels: function () {
            const progressBar = document.getElementById('progress-bar');
            const statusText = document.getElementById('loader-status');

            const models = [
                { id: 'cnc1', path: '/static/monitor/models/CNC01.glb' },
                { id: 'cnc2', path: '/static/monitor/models/CNC02.glb' },
                { id: 'cnc3', path: '/static/monitor/models/CNC03.glb' },
                { id: 'factory_scene', path: '/static/monitor/models/simple_factory_scene.glb' }
            ];

            const loader = new THREE.GLTFLoader();
            const totalModels = models.length;
            let loadedCount = 0;

            // 总体进度计算 (3个模型平均分布)
            const updateProgress = (idx, itemProgress) => {
                const totalProgress = ((loadedCount) / totalModels) * 100 + (itemProgress / totalModels);
                if (progressBar) progressBar.style.width = totalProgress + '%';
            };

            return Promise.all(models.map((m, index) => {
                return new Promise((resolve) => {
                    loader.load(m.path,
                        (gltf) => {
                            FactoryApp.state.cachedModels.set(m.id, gltf.scene);
                            loadedCount++;
                            if (statusText) statusText.innerText = `资源已就绪: ${m.id} (${loadedCount}/${totalModels})`;
                            updateProgress(index, 100);
                            resolve();
                        },
                        (xhr) => {
                            if (xhr.lengthComputable) {
                                const percent = (xhr.loaded / xhr.total) * 100;
                                updateProgress(index, percent);
                                if (statusText) statusText.innerText = `正在下载模型 ${m.id}: ${Math.round(percent)}%`;
                            }
                        },
                        (err) => {
                            console.error(`Failed to load ${m.id}:`, err);
                            loadedCount++; // 即使失败也继续
                            resolve();
                        }
                    );
                });
            }));
        }
    },

    // ══════════════════════════════════════
    // Socket & Data Sync
    // ══════════════════════════════════════
    socket: {
        connect: function () {
            const loc = window.location;
            const wsUrl = (loc.protocol === 'https:' ? 'wss://' : 'ws://') + loc.host + '/ws/factory/';
            const ws = new WebSocket(wsUrl);
            FactoryApp.state.ws = ws;

            ws.onopen = () => {
                console.log("[WS] Connected to Factory Stream");
                FactoryApp.ui.addLog("ENGINE: Connected to Real-time Stream.");
            };

            ws.onmessage = (e) => {
                const data = FactoryApp.utils.json_parse_safe(e.data);
                if (!data) return;

                const state = FactoryApp.state;
                if (data.type === 'factory_snapshot') {
                    state.currentDevices = data.devices.sort((a, b) => a.device_id - b.device_id);
                    FactoryApp.socket.sync3DStates();
                    FactoryApp.ui.updateStatsDisplay();
                    if (state.focusId) FactoryApp.ui.updateInspector(state.focusId);
                } else if (data.type === 'engine_log') {
                    FactoryApp.ui.addLog(data.message);
                } else if (data.type === 'device_mode_data') {
                    if (state.focusId && data.device_id == state.focusId) {
                        FactoryApp.ui.renderDeviceDetails(data);
                    }
                }
            };

            ws.onclose = () => {
                console.warn("[WS] Disconnected. Retrying in 3s...");
                setTimeout(FactoryApp.socket.connect, 3000);
            };
        },

        sync3DStates: function () {
            const state = FactoryApp.state;
            state.currentDevices.forEach((dev, idx) => {
                const meshId = idx + 1;
                const m = state.machines.get(meshId);
                if (m) {
                    m.group.userData.id = dev.device_id;
                    m.status = dev.current_status;
                    m.oee = dev.oee || 0;
                    if (dev.device_name && m.group.userData.name !== dev.device_name) {
                        m.group.userData.name = dev.device_name;
                        // 安全检查：仅在 labelPlate 存在时更新 (V4.3 已移除)
                        if (m.labelPlate && m.labelPlate.material) {
                            m.labelPlate.material.map = FactoryApp.utils.createSideLabelTex(dev.device_name);
                        }
                    }
                }
            });
        }
    },

    // ══════════════════════════════════════
    // Engine & Animation
    // ══════════════════════════════════════
    engine: {
        animate: function () {
            FactoryApp.state.animationId = requestAnimationFrame(FactoryApp.engine.animate);
            if (window.TWEEN) TWEEN.update();
            const state = FactoryApp.state;
            if (state.controls) state.controls.update();

            const delta = state.clock.getDelta();

            state.machines.forEach((m) => {
                let freq = 0.5;
                // 默认呼吸灯色：提亮为亮蓝色 (V4.5)
                let color = new THREE.Color(0x00d4ff);

                if (m.status === 'Running') {
                    if (m.oee > 0.75) freq = 1.2;
                    else if (m.oee >= 0.45) freq = 0.7;
                    else freq = 0.3;
                    color.set(0x00d4ff); // 运行态：高亮蓝
                } else if (m.status === 'Idle' || m.status === 'Standby') {
                    freq = 0.6;
                    color.set(0x00d4ff); // 待机态：也显示为蓝色（用户指定），但可通过动画区分
                } else if (m.status === 'Down' || m.status === 'Stop') {
                    freq = 1.0;
                    color.set(0xff4d4f); // 故障态：保留红色提示
                }

                if (m.status !== 'Offline') {
                    m.phase += delta * freq * Math.PI;
                    const pulse = (Math.sin(m.phase) + 1) / 2;

                    m.glow.material.color.copy(color);
                    // --- 提亮方案：大幅增强不透明度使其明显 ---
                    const baseOpacity = 1.0;
                    m.glow.material.opacity = pulse * baseOpacity;

                    // 安全检查：仅当识别到模型内部屏幕时执行高亮动画
                    if (m.windowScreen && m.windowScreen.material && m.windowScreen.material.emissive) {
                        if (m.status === 'Running') {
                            m.windowScreen.material.emissive.set(0x444d5d);
                            m.windowScreen.material.emissiveIntensity = pulse * 1.5;
                        } else {
                            m.windowScreen.material.emissiveIntensity = 0;
                        }
                    }
                } else {
                    m.glow.material.opacity = 0;
                    if (m.windowScreen && m.windowScreen.material) {
                        m.windowScreen.material.emissiveIntensity = 0;
                    }
                }
            });

            state.particles.forEach(p => {
                p.position.add(p.userData.vel);
                p.userData.life--;
                p.material.opacity = p.userData.life / 200;
                if (p.userData.life < 0) FactoryApp.engine.resetParticle(p);
            });

            if (state.renderer && state.scene && state.camera) {
                state.renderer.render(state.scene, state.camera);
            }
        },

        resetParticle: function (p) {
            p.position.set((Math.random() - 0.5) * 2000, 2, (Math.random() - 0.5) * 2000);
            p.userData.vel = new THREE.Vector3((Math.random() - 0.5) * 3, 0, (Math.random() - 0.5) * 3);
            p.userData.life = 100 + Math.random() * 200;
        },

        onResize: function () {
            const container = document.getElementById('canvasArea');
            if (!container) return;
            const state = FactoryApp.state;
            const aspect = container.clientWidth / container.clientHeight;
            const d = 1000;
            state.camera.left = -d * aspect; state.camera.right = d * aspect;
            state.camera.top = d; state.camera.bottom = -d;
            state.camera.updateProjectionMatrix();
            state.renderer.setSize(container.clientWidth, container.clientHeight);
        },

        onMouseMove: function (e) {
            const container = document.getElementById('canvasArea');
            const state = FactoryApp.state;
            const rect = container.getBoundingClientRect();
            state.mouse.x = ((e.clientX - rect.left) / container.clientWidth) * 2 - 1;
            state.mouse.y = -((e.clientY - rect.top) / container.clientHeight) * 2 + 1;

            state.raycaster.setFromCamera(state.mouse, state.camera);
            const intersects = state.raycaster.intersectObjects(state.scene.children, true);
            const tip = document.getElementById('tooltip');
            let hoverId = null;

            if (intersects.length > 0) {
                let obj = intersects[0].object;
                while (obj.parent && !obj.userData.id) obj = obj.parent;
                if (obj.userData.id) {
                    hoverId = obj.userData.id;
                    tip.style.display = 'block';
                    tip.style.left = (e.clientX - rect.left + 15) + 'px';
                    tip.style.top = (e.clientY - rect.top + 15) + 'px';
                    tip.innerText = obj.userData.name;
                    container.style.cursor = 'pointer';
                }
            }
            if (!hoverId) {
                if (tip) tip.style.display = 'none';
                container.style.cursor = 'default';
            }
        },

        onClick: function () {
            const state = FactoryApp.state;
            state.raycaster.setFromCamera(state.mouse, state.camera);
            const intersects = state.raycaster.intersectObjects(state.scene.children, true);
            if (intersects.length > 0) {
                let obj = intersects[0].object;
                // 向上追溯，直到找到带有 device_id 的父节点
                while (obj.parent && !obj.userData.id) {
                    obj = obj.parent;
                }

                if (obj.userData && obj.userData.id) {
                    FactoryApp.ui.focusMachine(obj.userData.id);
                } else {
                    FactoryApp.ui.unfocus(false); // 点击背景：原地不动，仅切换UI界面
                }
            } else {
                FactoryApp.ui.unfocus(false); // 点击空白：不回弹
            }
        },

        // ── 生命周期：销毁清理 (V3.3.2) ──
        destroy: function () {
            const state = FactoryApp.state;
            console.log("[FACTORY] Relinquishing GPU and Network resources...");

            // 1. 停止动画循环
            if (state.animationId) {
                cancelAnimationFrame(state.animationId);
            }

            // 2. 断开 WebSocket (禁用重连)
            if (state.ws) {
                state.ws.onclose = null;
                state.ws.close();
                state.ws = null;
            }

            // 3. 释放 Three.js 资源
            if (state.renderer) {
                state.renderer.dispose();
                // 强制丢失上下文以释放 GPU 显存
                const gl = state.renderer.getContext();
                const extension = gl.getExtension('WEBGL_lose_context');
                if (extension) extension.loseContext();

                const container = document.getElementById('canvasArea');
                if (container && state.renderer.domElement) {
                    container.removeChild(state.renderer.domElement);
                }
            }

            // 4. 移除全局事件监听
            window.removeEventListener('resize', FactoryApp.engine.onResize);
            state.isInitialized = false;
        }
    },

    // ══════════════════════════════════════
    // UI Interactions
    // ══════════════════════════════════════
    ui: {
        toggleUserMenu: function () {
            const menu = document.getElementById('user-dropdown');
            if (menu) menu.style.display = menu.style.display === 'none' ? 'block' : 'none';
        },

        addLog: function (msg) {
            const logList = document.getElementById('logList');
            if (!logList) return;
            const entry = document.createElement('div');
            entry.className = 'log-entry';

            if (msg.includes('OPTIMIZED') || msg.includes('SUCCESS') || msg.includes('ENGINE:')) {
                entry.style.color = 'var(--primary)';
                entry.style.fontWeight = '700';
            }

            const time = new Date().toLocaleTimeString('zh-CN', { hour12: false });
            entry.innerHTML = `<span style="color:#94a3b8; font-weight:400;">[${time}]</span> ${msg}`;
            logList.appendChild(entry);

            if (logList.children.length > 50) logList.firstChild.remove();

            const railBody = logList.parentElement;
            railBody.scrollTop = railBody.scrollHeight;
        },

        updateStatsDisplay: function () {
            const state = FactoryApp.state;
            const r = state.currentDevices.filter(d => d.current_status === 'Running').length;
            const s = state.currentDevices.filter(d => d.current_status === 'Down').length;
            const rEl = document.getElementById('st-run');
            const sEl = document.getElementById('st-std');
            const stEl = document.getElementById('st-stop');
            if (rEl) rEl.innerText = r;
            if (sEl) sEl.innerText = state.currentDevices.length - r - s;
            if (stEl) stEl.innerText = s;
        },

        focusMachine: function (id) {
            const state = FactoryApp.state;
            state.focusId = id;
            let m = null;
            for (let val of state.machines.values()) {
                if (val.group.userData.id == id) {
                    m = val;
                    break;
                }
            }
            if (!m) return;

            const targetPos = m.group.position;
            const camTarget = targetPos.clone().add(new THREE.Vector3(600, 500, 600));

            if (window.TWEEN) {
                TWEEN.removeAll();

                new TWEEN.Tween(state.camera.position)
                    .to({ x: camTarget.x, y: camTarget.y, z: camTarget.z }, 1000)
                    .easing(TWEEN.Easing.Quintic.Out)
                    .start();

                new TWEEN.Tween(state.controls.target)
                    .to({ x: targetPos.x, y: targetPos.y, z: targetPos.z }, 1000)
                    .easing(TWEEN.Easing.Quintic.Out)
                    .start();

                new TWEEN.Tween(state.camera)
                    .to({ zoom: 2.5 }, 1000)
                    .easing(TWEEN.Easing.Quintic.Out)
                    .onUpdate(() => state.camera.updateProjectionMatrix())
                    .start();
            }

            const labelEl = document.getElementById('railLabel');
            if (labelEl) labelEl.innerText = "Device Mode: " + id;
            const logEl = document.getElementById('logList');
            const insEl = document.getElementById('inspectEl');
            if (logEl) logEl.style.display = 'none';
            if (insEl) insEl.style.display = 'block';

            FactoryApp.ui.updateInspector(id);
            if (state.ws && state.ws.readyState === WebSocket.OPEN) {
                state.ws.send(JSON.stringify({
                    'action': 'fetch_device_history',
                    'device_id': id
                }));
            }
        },

        unfocus: function (forceReset = false) {
            const state = FactoryApp.state;
            state.focusId = null;
            if (window.TWEEN) {
                TWEEN.removeAll();

                // 只有点击 RETURN 或明确要求时才执行回弹动画
                if (forceReset) {
                    new TWEEN.Tween(state.camera.position)
                        .to({ x: 0, y: 1800, z: 2800 }, 1000)
                        .easing(TWEEN.Easing.Quintic.Out)
                        .start();

                    new TWEEN.Tween(state.controls.target)
                        .to({ x: 0, y: 0, z: 0 }, 1000)
                        .easing(TWEEN.Easing.Quintic.Out)
                        .start();
                }
            }

            const labelEl = document.getElementById('railLabel');
            if (labelEl) labelEl.innerText = "Engine Log";
            const logEl = document.getElementById('logList');
            const insEl = document.getElementById('inspectEl');
            if (logEl) logEl.style.display = 'block';
            if (insEl) insEl.style.display = 'none';
        },

        updateInspector: function (id) {
            const state = FactoryApp.state;
            const dev = state.currentDevices.find(d => d.device_id == id);
            if (!dev) return;

            const nameEl = document.getElementById('ins-name');
            if (nameEl) nameEl.innerText = dev.device_name;

            let statusZh = dev.current_status;
            if (statusZh === 'Running') statusZh = '运行中';
            else if (statusZh === 'Idle') statusZh = '待机中';
            else if (statusZh === 'Down') statusZh = '故障停机';

            // 确保 RETURN 按钮的逻辑独立且强力
            const btn = document.getElementById('ins-return');
            if (btn) {
                btn.onclick = (e) => {
                    if (e) e.stopPropagation(); // 阻止冒泡到背景 Canvas
                    console.log("[FactoryApp] Return Button Clicked -> Triggering Spring-Back");
                    FactoryApp.ui.unfocus(true);
                };
            }

            const statEl = document.getElementById('ins-stat');
            if (statEl) {
                statEl.innerText = statusZh;
                statEl.style.color = dev.current_status === 'Running' ? '#67c23a' :
                    (dev.current_status === 'Idle' ? '#E6A23C' : '#f56c6c');
            }

            const oeeEl = document.getElementById('ins-oee');
            const curEl = document.getElementById('ins-cur');
            const powEl = document.getElementById('ins-pow');
            if (oeeEl) oeeEl.innerText = (dev.oee * 100).toFixed(1) + '%';
            if (curEl) curEl.innerText = (dev.spindle_current || 0).toFixed(2) + ' A';
            if (powEl) powEl.innerText = (dev.spindle_power || 0).toFixed(2) + ' W';
        },

        renderDeviceDetails: function (data) {
            const advEl = document.getElementById('ins-advice');
            if (advEl) advEl.innerText = data.advice || '--';
            const histEl = document.getElementById('ins-history');
            if (!histEl) return;
            histEl.innerHTML = '';
            (data.history || []).forEach(h => {
                const row = document.createElement('div');
                row.style.borderBottom = '1px solid #f1f5f9';
                row.style.padding = '4px 0';
                row.innerHTML = `<span style="color:#94a3b8">[${h.time}]</span> Cur: ${h.cur}A | Pow: ${h.pow}W`;
                histEl.appendChild(row);
            });
        }
    },

    // ══════════════════════════════════════
    // Utilities & Textures
    // ══════════════════════════════════════
    utils: {
        json_parse_safe: function (str) {
            try { return JSON.parse(str); } catch (e) { return null; }
        },

        createGlowTex: function () {
            const canvas = document.createElement('canvas');
            canvas.width = 128; canvas.height = 128;
            const context = canvas.getContext('2d');
            const grad = context.createRadialGradient(64, 64, 0, 64, 64, 64);
            grad.addColorStop(0, 'rgba(255, 255, 255, 1)');
            grad.addColorStop(0.2, 'rgba(255, 255, 255, 0.8)');
            grad.addColorStop(0.5, 'rgba(255, 255, 255, 0.3)');
            grad.addColorStop(1, 'rgba(255, 255, 255, 0)');
            context.fillStyle = grad;
            context.fillRect(0, 0, 128, 128);
            return new THREE.CanvasTexture(canvas);
        },

        createSideLabelTex: function (text) {
            const canvas = document.createElement('canvas');
            canvas.width = 512; canvas.height = 128;
            const ctx = canvas.getContext('2d');
            ctx.fillStyle = '#0f172a';
            ctx.fillRect(0, 0, 512, 128);
            ctx.font = 'bold 56px "Menlo", "Courier New", monospace';
            ctx.fillStyle = '#ffffff';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText(text, 256, 64, 480);
            return new THREE.CanvasTexture(canvas);
        },

        // --- 风格重塑：动态几何电路纹理生成器 (V5.0) ---
        createCircuitTex: function () {
            const canvas = document.createElement('canvas');
            canvas.width = 1024; canvas.height = 1024;
            const ctx = canvas.getContext('2d');

            // 底色：深灰蓝
            ctx.fillStyle = '#1e293b';
            ctx.fillRect(0, 0, 1024, 1024);

            // 第一层：极细网格底噪
            ctx.strokeStyle = 'rgba(148, 163, 184, 0.05)';
            ctx.lineWidth = 1;
            for (let i = 0; i < 1024; i += 32) {
                ctx.beginPath(); ctx.moveTo(i, 0); ctx.lineTo(i, 1024); ctx.stroke();
                ctx.beginPath(); ctx.moveTo(0, i); ctx.lineTo(1024, i); ctx.stroke();
            }

            // 第二层：不规则几何折线 (电路走线)
            const colors = ['#409eff', '#00d4ff', '#94a3b8'];
            for (let i = 0; i < 40; i++) {
                ctx.strokeStyle = colors[Math.floor(Math.random() * colors.length)];
                ctx.globalAlpha = Math.random() * 0.4 + 0.1;
                ctx.lineWidth = Math.random() * 2 + 1;

                let curX = Math.floor(Math.random() * 32) * 32;
                let curY = Math.floor(Math.random() * 32) * 32;

                ctx.beginPath();
                ctx.moveTo(curX, curY);

                // 走 2-4 个折弯
                const segments = Math.floor(Math.random() * 3) + 2;
                for (let j = 0; j < segments; j++) {
                    const isX = Math.random() > 0.5;
                    const dist = (Math.floor(Math.random() * 6) + 1) * 32;
                    if (isX) curX += (Math.random() > 0.5 ? dist : -dist);
                    else curY += (Math.random() > 0.5 ? dist : -dist);
                    ctx.lineTo(curX, curY);
                }
                ctx.stroke();

                // 特定位置绘制科技圆点 (节点)
                if (Math.random() > 0.6) {
                    ctx.fillStyle = ctx.strokeStyle;
                    ctx.beginPath();
                    ctx.arc(curX, curY, 4, 0, Math.PI * 2);
                    ctx.fill();
                }
            }

            const tex = new THREE.CanvasTexture(canvas);
            tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
            tex.repeat.set(4, 4); // 进行 4x4 阵列平铺
            return tex;
        }
    }
};

// Global click handler to close menu
document.addEventListener('click', function (e) {
    const btn = document.getElementById('user-avatar-btn');
    const menu = document.getElementById('user-dropdown');
    if (menu && btn && !btn.contains(e.target) && !menu.contains(e.target)) {
        menu.style.display = 'none';
    }
});

// Bootstrapper
document.addEventListener('DOMContentLoaded', () => {
    FactoryApp.scene.init();
});

// 监听页面卸载执行清理 (方案 B 最小化适配)
window.addEventListener('beforeunload', () => {
    if (window.FactoryApp && FactoryApp.engine.destroy) {
        FactoryApp.engine.destroy();
    }
});
