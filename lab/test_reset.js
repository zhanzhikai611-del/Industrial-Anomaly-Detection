const { chromium } = require('playwright');
(async () => {
    try {
        const browser = await chromium.launch();
        const page = await browser.newPage();
        
        page.on('console', msg => console.log('LOG:', msg.text()));
        page.on('pageerror', err => console.log('ERROR:', err.message));
        
        await page.goto('http://127.0.0.1:8000/dashboard/');
        
        // Login if needed
        if (await page.$('#id_username')) {
            await page.fill('#id_username', 'admin');
            await page.fill('#id_password', 'admin');
            await page.click('button[type="submit"]');
            await page.waitForNavigation();
        }

        console.log("Navigating to settings via HTMX");
        await page.evaluate(() => document.querySelector('a[href="/setting/"]').click());
        await page.waitForTimeout(2000); // Wait for HTMX
        
        // Wait and find global-modals-area
        console.log("Checking reset modal...");
        let display = await page.evaluate(() => document.getElementById('reset-modal').style.display);
        console.log("Initial modal display:", display);
        
        // Click immediate reset
        console.log("Clicking '立即重置' ...");
        await page.evaluate(() => {
            const btns = Array.from(document.querySelectorAll('button'));
            const btn = btns.find(b => b.textContent && b.textContent.includes('立即重置'));
            if(btn) btn.click();
        });
        await page.waitForTimeout(500);
        
        display = await page.evaluate(() => document.getElementById('reset-modal').style.display);
        console.log("Modal display after click:", display);
        
        console.log("Clicking '取消' ...");
        await page.evaluate(() => {
            const btns = Array.from(document.querySelectorAll('.btn-cancel'));
            const btn = btns.find(b => b.textContent && b.textContent.includes('取消'));
            if(btn) btn.click();
        });
        await page.waitForTimeout(500);
        
        display = await page.evaluate(() => document.getElementById('reset-modal').style.display);
        console.log("Modal display after cancel:", display);
        
        const alpineData = await page.evaluate(() => {
            if(!window.Alpine) return 'NO ALPINE';
            const el = document.getElementById('global-modals-area').firstElementChild;
            // Get proxy
            return Alpine.$data(el).showResetModal;
        });
        console.log("Alpine internal showResetModal:", alpineData);
        
        await browser.close();
    } catch(e) {
        console.log("Fatal Error:", e.message);
    }
})();
