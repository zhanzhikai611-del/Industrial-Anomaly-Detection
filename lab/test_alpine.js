const { chromium } = require('playwright');
(async () => {
    let process_exit = 0;
    try {
        const browser = await chromium.launch();
        const page = await browser.newPage();
        
        page.on('console', msg => console.log('PAGE LOG:', msg.text()));
        page.on('pageerror', err => console.log('PAGE ERROR:', err.message));
        
        await page.goto('http://127.0.0.1:8000/dashboard/');
        
        if (await page.$('#id_username')) {
            await page.fill('#id_username', 'admin');
            await page.fill('#id_password', 'admin');
            await page.click('button[type="submit"]');
            await page.waitForNavigation();
        }

        console.log("Navigating to accounts via HTMX");
        await page.evaluate(() => document.querySelector('a[href="/accounts/"]').click());
        await page.waitForTimeout(2000);
        
        await browser.close();
    } catch(e) {
        console.log("Error:", e.message);
    }
})();
