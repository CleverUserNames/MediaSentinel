package com.mediasentinel;

import android.app.Activity;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.KeyEvent;
import android.view.View;
import android.webkit.JavascriptInterface;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.ImageButton;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;

public class MainActivity extends Activity {

    private WebView webView;
    private ProgressBar progressBar;
    private TextView statusText;
    private LinearLayout tabBar;
    private LinearLayout errorView;
    private TextView errorMessage;
    private ImageButton refreshBtn;
    private ImageButton settingsBtn;

    private String serverUrl;
    private static final String PREFS_NAME   = "MediaSentinelPrefs";
    private static final String PREF_SERVER  = "server_url";
    private static final String PREF_SEER    = "seer_url";
    private static final String DEFAULT_SERVER = "http://192.168.1.100:8765";
    private static final String DEFAULT_SEER   = "http://192.168.1.100:5055";

    private final String[] SECTION_NAMES = {"Issues", "Transcodes", "Seerr", "Picks", "Drives", "Tips"};
    private final String[] SECTION_IDS   = {"issues-section","transcode-section","seer-section","rec-section","drive-section","suggestions-section"};

    private int currentTab    = 0;
    private int focusMode     = 0; // 0=header 1=content
    private View[] tabViews;
    private Handler handler   = new Handler(Looper.getMainLooper());

    // JavaScript injected into the report for full D-pad control
    private static final String TV_JS = ""
        + "(function(){"
        // Add section IDs
        + "var titles=document.querySelectorAll('.section-title');"
        + "var ids=['issues-section','transcode-section','seer-section','rec-section','drive-section','suggestions-section'];"
        + "for(var i=0;i<titles.length&&i<ids.length;i++){titles[i].id=ids[i];}"

        // Make all issue cards and rec cards focusable
        + "var cards=document.querySelectorAll('.issue-card,div[style*=\"border-left:4px\"],div[style*=\"border-radius:var(--radius)\"]');"
        + "cards.forEach(function(c){c.setAttribute('tabindex','0');});"

        // Make all links/buttons in report focusable and styled for TV
        + "var btns=document.querySelectorAll('a[href]');"
        + "btns.forEach(function(b){"
        + "  b.setAttribute('tabindex','0');"
        + "  if(!b.textContent.trim()){b.textContent='Request';}"
        + "  b.style.display='inline-block';"
        + "  b.style.padding='8px 16px';"
        + "  b.style.borderRadius='6px';"
        + "  b.style.textDecoration='none';"
        + "  b.style.fontSize='14px';"
        + "  b.style.fontWeight='600';"
        + "  b.style.color='#0f172a';"
        + "  b.style.background='#38bdf8';"
        + "});"

        // Add focus highlight style
        + "var style=document.createElement('style');"
        + "style.innerHTML="
        + "'*:focus{outline:3px solid #38bdf8!important;outline-offset:2px!important;}'"
        + "+'body{font-size:16px!important;}'"
        + "+'a[href]:focus{background:#7dd3fc!important;outline:3px solid #fff!important;}'"
        + "+'div[tabindex]:focus{outline:3px solid #38bdf8!important;}'"
        + "+'div[style*=grid]{grid-template-columns:repeat(auto-fill,minmax(160px,1fr))!important;}'"
        + "+'img{max-width:100%!important;}';"
        + "document.head.appendChild(style);"

        // Track focused element index for D-pad navigation
        + "window._focusables=[];"
        + "window._focusIdx=-1;"
        + "function refreshFocusables(){"
        + "  window._focusables=Array.from(document.querySelectorAll('a[href],button,[tabindex=\"0\"]'));"
        + "}"
        + "refreshFocusables();"

        + "window._navigateFocus=function(dir){"
        + "  refreshFocusables();"
        + "  var el=document.activeElement;"
        + "  var idx=window._focusables.indexOf(el);"
        + "  if(dir==='down'){idx=idx<0?0:Math.min(idx+1,window._focusables.length-1);}"
        + "  else if(dir==='up'){idx=idx<=0?0:idx-1;}"
        + "  else if(dir==='right'){idx=idx<0?0:Math.min(idx+1,window._focusables.length-1);}"
        + "  else if(dir==='left'){idx=idx<=0?0:idx-1;}"
        + "  if(window._focusables[idx]){"
        + "    window._focusables[idx].focus();"
        + "    window._focusables[idx].scrollIntoView({behavior:'smooth',block:'center'});"
        + "    window._focusIdx=idx;"
        + "    return true;"
        + "  }"
        + "  return false;"
        + "};"

        + "window._clickFocused=function(){"
        + "  var el=document.activeElement;"
        + "  if(el&&el!==document.body){el.click();return true;}"
        + "  return false;"
        + "};"

        + "window._scrollPage=function(dy){"
        + "  window.scrollBy({top:dy,behavior:'smooth'});"
        + "};"

        + "window._jumpToSection=function(id){"
        + "  var el=document.getElementById(id);"
        + "  if(el){el.scrollIntoView({behavior:'smooth',block:'start'});"
        + "  var focusable=el.nextElementSibling;"
        + "  while(focusable&&!focusable.matches('a,[tabindex]')){focusable=focusable.querySelector('a,[tabindex]');break;}"
        + "  if(focusable){focusable.focus();window._focusIdx=window._focusables.indexOf(focusable);}}"
        + "};"

        + "})();";

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        SharedPreferences prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE);
        serverUrl = prefs.getString(PREF_SERVER, DEFAULT_SERVER);
        setContentView(R.layout.activity_main);

        webView      = findViewById(R.id.webView);
        progressBar  = findViewById(R.id.progressBar);
        statusText   = findViewById(R.id.statusText);
        tabBar       = findViewById(R.id.tabBar);
        errorView    = findViewById(R.id.errorView);
        errorMessage = findViewById(R.id.errorMessage);
        refreshBtn   = findViewById(R.id.refreshButton);
        settingsBtn  = findViewById(R.id.settingsButton);

        setupWebView();
        setupTabBar();
        setupButtons();
        loadReport();

        // Start in header mode
        focusMode = 0;
        tabViews[0].requestFocus();
    }

    private void setupWebView() {
        WebSettings s = webView.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setLoadWithOverviewMode(true);
        s.setUseWideViewPort(true);
        s.setBuiltInZoomControls(false);
        s.setDisplayZoomControls(false);
        s.setTextZoom(110);
        webView.setBackgroundColor(Color.parseColor("#0f172a"));
        webView.setScrollBarStyle(View.SCROLLBARS_INSIDE_OVERLAY);
        webView.setFocusable(false);
        webView.setFocusableInTouchMode(false);

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest req) {
                String url = req.getUrl().toString();
                // Any link that isn't on the report server opens in the Jellyseer overlay
                if (!url.startsWith(serverUrl)) {
                    openSeerOverlay(url);
                    return true;
                }
                return false;
            }

            @Override
            public void onPageStarted(WebView v, String url, android.graphics.Bitmap fav) {
                progressBar.setVisibility(View.VISIBLE);
                errorView.setVisibility(View.GONE);
                statusText.setText("Loading...");
            }
            @Override
            public void onPageFinished(WebView v, String url) {
                progressBar.setVisibility(View.GONE);
                statusText.setText("MediaSentinel");
                webView.evaluateJavascript(TV_JS, null);
            }
            @Override
            public void onReceivedError(WebView v, WebResourceRequest req, WebResourceError err) {
                if (req.isForMainFrame()) {
                    progressBar.setVisibility(View.GONE);
                    errorView.setVisibility(View.VISIBLE);
                    errorMessage.setText("Cannot reach:\n" + serverUrl +
                            "\n\nCheck that the HTTP server is running\nand port 8765 firewall is open.");
                }
            }
        });
    }

    private String getSeerUrlFromConfig() {
        SharedPreferences prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE);
        return prefs.getString(PREF_SEER, DEFAULT_SEER);
    }

    private android.widget.FrameLayout seerOverlay;
    private WebView seerWebView;

    private void openSeerOverlay(String url) {
        // Create a full-screen overlay WebView for Jellyseer
        if (seerOverlay == null) {
            seerOverlay = new android.widget.FrameLayout(this);
            seerOverlay.setBackgroundColor(Color.parseColor("#0f172a"));

            seerWebView = new WebView(this);
            WebSettings ss = seerWebView.getSettings();
            ss.setJavaScriptEnabled(true);
            ss.setDomStorageEnabled(true);
            ss.setUseWideViewPort(true);
            ss.setLoadWithOverviewMode(true);
            seerWebView.setBackgroundColor(Color.parseColor("#0f172a"));
            seerWebView.setWebViewClient(new WebViewClient() {
                @Override
                public void onPageFinished(WebView v, String u) {
                    // Inject TV-friendly styles into Jellyseer
                    String js = "(function(){"
                        + "var style=document.createElement('style');"
                        + "style.innerHTML='body{font-size:18px!important;}*:focus{outline:3px solid #38bdf8!important;}';"
                        + "document.head.appendChild(style);"
                        + "})();";
                    v.evaluateJavascript(js, null);
                }
            });

            // Close button
            android.widget.Button closeBtn = new android.widget.Button(this);
            closeBtn.setText("Back (BACK button)");
            closeBtn.setTextColor(Color.WHITE);
            closeBtn.setBackgroundColor(Color.parseColor("#1e293b"));
            closeBtn.setOnClickListener(v -> closeSeerOverlay());
            closeBtn.setPadding(20, 10, 20, 10);

            android.widget.FrameLayout.LayoutParams webParams = new android.widget.FrameLayout.LayoutParams(
                android.widget.FrameLayout.LayoutParams.MATCH_PARENT,
                android.widget.FrameLayout.LayoutParams.MATCH_PARENT
            );
            webParams.topMargin = 60;

            android.widget.FrameLayout.LayoutParams btnParams = new android.widget.FrameLayout.LayoutParams(
                android.widget.FrameLayout.LayoutParams.WRAP_CONTENT,
                60
            );
            btnParams.gravity = android.view.Gravity.TOP | android.view.Gravity.START;

            seerOverlay.addView(seerWebView, webParams);
            seerOverlay.addView(closeBtn, btnParams);

            android.widget.FrameLayout root = (android.widget.FrameLayout)
                getWindow().getDecorView().getRootView();
            root.addView(seerOverlay, new android.widget.FrameLayout.LayoutParams(
                android.widget.FrameLayout.LayoutParams.MATCH_PARENT,
                android.widget.FrameLayout.LayoutParams.MATCH_PARENT
            ));
        }

        seerWebView.loadUrl(url);
        seerOverlay.setVisibility(View.VISIBLE);
        seerWebView.requestFocus();
        Toast.makeText(this, "Opening in Jellyseer - press BACK to return", Toast.LENGTH_LONG).show();
    }

    private void closeSeerOverlay() {
        if (seerOverlay != null) {
            seerOverlay.setVisibility(View.GONE);
            tabViews[currentTab].requestFocus();
        }
    }

    private void setupTabBar() {
        tabViews = new View[SECTION_NAMES.length];
        for (int i = 0; i < SECTION_NAMES.length; i++) {
            final int idx = i;
            TextView tab = new TextView(this);
            tab.setText(SECTION_NAMES[i]);
            tab.setTextColor(Color.parseColor("#94a3b8"));
            tab.setTextSize(13);
            tab.setPadding(20, 12, 20, 12);
            tab.setFocusable(true);
            tab.setClickable(true);
            tab.setBackground(getDrawable(R.drawable.tab_background));
            tab.setNextFocusRightId(idx < SECTION_NAMES.length - 1 ? View.NO_ID : R.id.refreshButton);

            tab.setOnClickListener(v -> jumpToSection(idx));
            tab.setOnFocusChangeListener((v, hasFocus) -> {
                TextView t = (TextView) v;
                if (hasFocus) {
                    currentTab = idx;
                    t.setTextColor(Color.WHITE);
                    t.setBackground(getDrawable(R.drawable.tab_selected));
                } else {
                    if (idx != currentTab) {
                        t.setTextColor(Color.parseColor("#94a3b8"));
                        t.setBackground(getDrawable(R.drawable.tab_background));
                    }
                }
            });

            tabBar.addView(tab);
            tabViews[i] = tab;

            // Chain focus between tabs
            if (i > 0) {
                tabViews[i - 1].setNextFocusRightId(tab.getId());
                tab.setNextFocusLeftId(tabViews[i - 1].getId());
            }
        }
        updateTabHighlight();
    }

    private void setupButtons() {
        refreshBtn.setFocusable(true);
        refreshBtn.setNextFocusLeftId(R.id.settingsButton);
        refreshBtn.setOnClickListener(v -> {
            loadReport();
            Toast.makeText(this, "Refreshing...", Toast.LENGTH_SHORT).show();
        });
        refreshBtn.setOnFocusChangeListener((v, f) -> refreshBtn.setAlpha(f ? 1f : 0.6f));

        settingsBtn.setFocusable(true);
        settingsBtn.setNextFocusRightId(R.id.refreshButton);
        settingsBtn.setOnClickListener(v ->
                startActivity(new Intent(this, SettingsActivity.class)));
        settingsBtn.setOnFocusChangeListener((v, f) -> settingsBtn.setAlpha(f ? 1f : 0.6f));
    }

    private void jumpToSection(int idx) {
        currentTab = idx;
        updateTabHighlight();
        webView.evaluateJavascript("window._jumpToSection('" + SECTION_IDS[idx] + "');", null);
    }

    private void updateTabHighlight() {
        for (int i = 0; i < tabViews.length; i++) {
            TextView t = (TextView) tabViews[i];
            if (i == currentTab) {
                t.setTextColor(Color.parseColor("#0f172a"));
                t.setBackground(getDrawable(R.drawable.tab_selected));
            } else {
                t.setTextColor(Color.parseColor("#94a3b8"));
                t.setBackground(getDrawable(R.drawable.tab_background));
            }
        }
    }

    private void loadReport() {
        SharedPreferences prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE);
        serverUrl = prefs.getString(PREF_SERVER, DEFAULT_SERVER);
        errorView.setVisibility(View.GONE);
        webView.loadUrl(serverUrl + "/report_latest.html");
    }

    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {

        // ── Header mode: navigate tabs and buttons ────────────────────
        if (focusMode == 0) {
            switch (keyCode) {
                case KeyEvent.KEYCODE_DPAD_DOWN:
                    // Enter content mode
                    focusMode = 1;
                    webView.evaluateJavascript("window._jumpToSection('" + SECTION_IDS[currentTab] + "');", null);
                    return true;

                case KeyEvent.KEYCODE_DPAD_LEFT:
                    View cur = getCurrentFocus();
                    if (cur == refreshBtn) {
                        settingsBtn.requestFocus();
                    } else if (cur == settingsBtn) {
                        tabViews[tabViews.length - 1].requestFocus();
                    } else if (currentTab > 0) {
                        tabViews[--currentTab].requestFocus();
                        updateTabHighlight();
                    }
                    return true;

                case KeyEvent.KEYCODE_DPAD_RIGHT:
                    View cur2 = getCurrentFocus();
                    if (cur2 == settingsBtn) {
                        refreshBtn.requestFocus();
                    } else if (cur2 == refreshBtn) {
                        // stay
                    } else if (currentTab < SECTION_NAMES.length - 1) {
                        tabViews[++currentTab].requestFocus();
                        updateTabHighlight();
                    } else {
                        settingsBtn.requestFocus();
                    }
                    return true;

                case KeyEvent.KEYCODE_DPAD_CENTER:
                case KeyEvent.KEYCODE_ENTER:
                    View focused = getCurrentFocus();
                    if (focused != null) focused.performClick();
                    return true;
            }
        }

        // ── Content mode: navigate within WebView ─────────────────────
        if (focusMode == 1) {
            switch (keyCode) {
                case KeyEvent.KEYCODE_DPAD_UP:
                    // Check if at top - go back to header
                    webView.evaluateJavascript("window.scrollY", val -> {
                        float scrollY = Float.parseFloat(val);
                        if (scrollY < 50) {
                            runOnUiThread(() -> {
                                focusMode = 0;
                                tabViews[currentTab].requestFocus();
                            });
                        } else {
                            webView.evaluateJavascript("window._navigateFocus('up');", result -> {
                                if ("false".equals(result)) {
                                    webView.evaluateJavascript("window._scrollPage(-250);", null);
                                }
                            });
                        }
                    });
                    return true;

                case KeyEvent.KEYCODE_DPAD_DOWN:
                    webView.evaluateJavascript("window._navigateFocus('down');", result -> {
                        if ("false".equals(result)) {
                            webView.evaluateJavascript("window._scrollPage(250);", null);
                        }
                    });
                    return true;

                case KeyEvent.KEYCODE_DPAD_LEFT:
                    webView.evaluateJavascript("window._navigateFocus('left');", null);
                    return true;

                case KeyEvent.KEYCODE_DPAD_RIGHT:
                    webView.evaluateJavascript("window._navigateFocus('right');", null);
                    return true;

                case KeyEvent.KEYCODE_DPAD_CENTER:
                case KeyEvent.KEYCODE_ENTER:
                    webView.evaluateJavascript("window._clickFocused();", result -> {
                        if ("false".equals(result)) {
                            webView.evaluateJavascript("window._scrollPage(400);", null);
                        }
                    });
                    return true;

                case KeyEvent.KEYCODE_BUTTON_Y:
                case KeyEvent.KEYCODE_F5:
                    loadReport();
                    Toast.makeText(this, "Refreshing...", Toast.LENGTH_SHORT).show();
                    return true;

                case KeyEvent.KEYCODE_PAGE_UP:
                case KeyEvent.KEYCODE_BUTTON_L1:
                    webView.evaluateJavascript("window._scrollPage(-600);", null);
                    return true;

                case KeyEvent.KEYCODE_PAGE_DOWN:
                case KeyEvent.KEYCODE_BUTTON_R1:
                    webView.evaluateJavascript("window._scrollPage(600);", null);
                    return true;
            }
        }

        // ── Global keys ───────────────────────────────────────────────
        switch (keyCode) {
            case KeyEvent.KEYCODE_BUTTON_Y:
                loadReport();
                Toast.makeText(this, "Refreshing...", Toast.LENGTH_SHORT).show();
                return true;
            case KeyEvent.KEYCODE_BACK:
                if (seerOverlay != null && seerOverlay.getVisibility() == View.VISIBLE) {
                    closeSeerOverlay();
                    return true;
                }
                if (focusMode == 1) {
                    focusMode = 0;
                    tabViews[currentTab].requestFocus();
                    return true;
                }
                break;
        }
        return super.onKeyDown(keyCode, event);
    }

    @Override
    protected void onResume() {
        super.onResume();
        SharedPreferences prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE);
        String newUrl = prefs.getString(PREF_SERVER, DEFAULT_SERVER);
        if (!newUrl.equals(serverUrl)) {
            serverUrl = newUrl;
            loadReport();
        }
    }
}
