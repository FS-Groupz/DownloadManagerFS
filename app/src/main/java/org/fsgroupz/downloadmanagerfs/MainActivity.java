package org.fsgroupz.downloadmanagerfs;

import android.app.Activity;
import android.app.DownloadManager;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.webkit.CookieManager;
import android.webkit.DownloadListener;
import android.webkit.JavascriptInterface;
import android.webkit.URLUtil;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Toast;
import android.content.ContentUris;
import android.database.Cursor;
import android.provider.MediaStore;

public class MainActivity extends Activity {

    private WebView webView;
    private static final int PERMISSION_REQ_CODE = 1001;
    private String pendingSharedUrl = null;
    private boolean isPageLoaded = false;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        webView = findViewById(R.id.webview);

        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(true);
        settings.setAllowContentAccess(true);
        settings.setDatabaseEnabled(true);
        settings.setLoadWithOverviewMode(true);
        settings.setUseWideViewPort(true);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);

        // Allow fetching external APIs and local assets seamlessly
        try {
            settings.setAllowFileAccessFromFileURLs(true);
            settings.setAllowUniversalAccessFromFileURLs(true);
        } catch (Exception ignored) {}

        // Enable USB WebView Debugging for Android Studio & Chrome DevTools
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.KITKAT) {
            WebView.setWebContentsDebuggingEnabled(true);
        }

        // JavaScript Interface for Native Android Integration
        webView.addJavascriptInterface(new AndroidBridge(this), "AndroidApp");

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, String url) {
                // If it is the sponsored PetsHeaven link or external shopping link, open in native browser
                if (url != null && url.contains("petsheaven.in")) {
                    try {
                        Intent intent = new Intent(Intent.ACTION_VIEW, Uri.parse(url));
                        startActivity(intent);
                        return true;
                    } catch (Exception e) {
                        Toast.makeText(MainActivity.this, "Opening PetsHeaven...", Toast.LENGTH_SHORT).show();
                    }
                }
                return false;
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                super.onPageFinished(view, url);
                isPageLoaded = true;
                if (pendingSharedUrl != null) {
                    dispatchSharedUrl(pendingSharedUrl);
                    pendingSharedUrl = null;
                }
            }
        });

        // Android native DownloadManager integration for standard links
        webView.setDownloadListener(new DownloadListener() {
            @Override
            public void onDownloadStart(String url, String userAgent, String contentDisposition, String mimeType, long contentLength) {
                startNativeDownload(url, URLUtil.guessFileName(url, contentDisposition, mimeType), mimeType);
            }
        });

        // Check storage permissions on Android 6 to 9
        checkStoragePermissions();

        // Check if app was opened via Share button (YouTube, Instagram, etc.)
        handleIncomingIntent(getIntent());

        // Load bundled responsive mobile interface
        webView.loadUrl("file:///android_asset/index.html");
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        handleIncomingIntent(intent);
    }

    private void handleIncomingIntent(Intent intent) {
        if (intent == null) return;
        String action = intent.getAction();
        String type = intent.getType();

        String rawText = null;
        if (Intent.ACTION_SEND.equals(action) && type != null) {
            if (type.startsWith("text/")) {
                rawText = intent.getStringExtra(Intent.EXTRA_TEXT);
            }
        } else if (Intent.ACTION_VIEW.equals(action)) {
            Uri data = intent.getData();
            if (data != null) {
                rawText = data.toString();
            }
        }

        if (rawText != null) {
            String extractedUrl = extractUrl(rawText);
            if (extractedUrl != null && !extractedUrl.isEmpty()) {
                pendingSharedUrl = extractedUrl;
                if (isPageLoaded && webView != null) {
                    dispatchSharedUrl(extractedUrl);
                    pendingSharedUrl = null;
                }
            }
        }
    }

    private String extractUrl(String text) {
        if (text == null) return null;
        java.util.regex.Pattern p = java.util.regex.Pattern.compile("https?://[\\w\\.\\-/?#&=%~+:;@!*$,_]+");
        java.util.regex.Matcher m = p.matcher(text);
        if (m.find()) {
            return m.group(0);
        }
        return text.trim();
    }

    private void dispatchSharedUrl(final String url) {
        runOnUiThread(() -> {
            if (webView != null) {
                String escaped = url.replace("'", "\\'");
                webView.evaluateJavascript("if (typeof window.onSharedUrlReceived === 'function') { window.onSharedUrlReceived('" + escaped + "'); }", null);
            }
        });
    }

    private void checkStoragePermissions() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M && Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) {
            if (checkSelfPermission(android.Manifest.permission.WRITE_EXTERNAL_STORAGE) != PackageManager.PERMISSION_GRANTED) {
                requestPermissions(new String[]{
                        android.Manifest.permission.WRITE_EXTERNAL_STORAGE,
                        android.Manifest.permission.READ_EXTERNAL_STORAGE
                }, PERMISSION_REQ_CODE);
            }
        }
    }

    public void startNativeDownload(String url, String filename, String mimeType) {
        try {
            if (url == null || url.trim().isEmpty()) {
                Toast.makeText(getApplicationContext(), "Invalid download URL", Toast.LENGTH_SHORT).show();
                return;
            }
            url = url.trim();

            DownloadManager.Request request = new DownloadManager.Request(Uri.parse(url));
            if (mimeType != null && !mimeType.isEmpty()) {
                request.setMimeType(mimeType);
            }
            String cookies = CookieManager.getInstance().getCookie(url);
            if (cookies != null) {
                request.addRequestHeader("cookie", cookies);
            }
            request.addRequestHeader("User-Agent", "Mozilla/5.0 (Linux; Android 10; Mobile) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36");
            request.setDescription("Download Manager FS - Downloading file...");

            String safeFilename = (filename != null && !filename.trim().isEmpty())
                    ? filename.trim()
                    : URLUtil.guessFileName(url, null, mimeType);
            // Sanitize file name for Android storage
            safeFilename = safeFilename.replaceAll("[^a-zA-Z0-9._ -]", "_").replaceAll("[_ ]+", "_").trim();
            if (safeFilename.isEmpty()) { safeFilename = "video_" + System.currentTimeMillis(); }
            
            // Ensure appropriate extension if missing
            if (!safeFilename.contains(".")) {
                if (mimeType != null && (mimeType.contains("audio") || mimeType.contains("mp3"))) {
                    safeFilename += ".mp3";
                } else {
                    safeFilename += ".mp4";
                }
            }

            request.setTitle(safeFilename);
            request.allowScanningByMediaScanner();
            request.setAllowedNetworkTypes(DownloadManager.Request.NETWORK_WIFI | DownloadManager.Request.NETWORK_MOBILE);
            request.setAllowedOverRoaming(true);
            request.setAllowedOverMetered(true);
            request.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
            request.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, safeFilename);

            DownloadManager dm = (DownloadManager) getSystemService(Context.DOWNLOAD_SERVICE);
            if (dm != null) {
                dm.enqueue(request);
                Toast.makeText(getApplicationContext(), "📥 Download started: " + safeFilename + "\nCheck notification bar!", Toast.LENGTH_LONG).show();
            }
        } catch (Exception e) {
            Toast.makeText(getApplicationContext(), "Download error: " + e.getMessage(), Toast.LENGTH_LONG).show();
        }
    }

    public class AndroidBridge {
        Context context;

        AndroidBridge(Context ctx) {
            context = ctx;
        }

        @JavascriptInterface
        public boolean isNativeApp() {
            return true;
        }

        @JavascriptInterface
        public String getPendingSharedUrl() {
            String url = pendingSharedUrl;
            pendingSharedUrl = null;
            return url != null ? url : "";
        }

        @JavascriptInterface
        public void downloadFile(String url, String filename) {
            String mime = "video/mp4";
            if (filename != null && (filename.endsWith(".mp3") || filename.endsWith(".m4a"))) { mime = "audio/mpeg"; }
            final String finalMime = mime;
            runOnUiThread(() -> startNativeDownload(url, filename, finalMime));
        }

        @JavascriptInterface
        public void downloadFileWithMime(String url, String filename, String mimeType) {
            runOnUiThread(() -> startNativeDownload(url, filename, mimeType));
        }

        @JavascriptInterface
        public void openBrowser(String url) {
            runOnUiThread(() -> {
                try {
                    Intent intent = new Intent(Intent.ACTION_VIEW, Uri.parse(url));
                    startActivity(intent);
                } catch (Exception e) {
                    Toast.makeText(context, "Error opening link: " + e.getMessage(), Toast.LENGTH_SHORT).show();
                }
            });
        }

        @JavascriptInterface
        public void showToast(String message) {
            runOnUiThread(() -> Toast.makeText(context, message, Toast.LENGTH_SHORT).show());
        }

        @JavascriptInterface
        public void openDownloadsFolder() {
            runOnUiThread(() -> {
                try {
                    Intent intent = new Intent(DownloadManager.ACTION_VIEW_DOWNLOADS);
                    intent.setFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                    context.startActivity(intent);
                } catch (Exception e) {
                    Toast.makeText(context, "Downloads folder is in your Files app", Toast.LENGTH_SHORT).show();
                }
            });
        }

        @JavascriptInterface
        public void openDownloadedFile(String filename) {
            runOnUiThread(() -> {
                try {
                    Uri mediaUri = null;
                    String cleanName = filename != null ? filename.replaceAll("[^a-zA-Z0-9]", "%") : "";
                    String selection = MediaStore.Video.Media.DISPLAY_NAME + " LIKE ?";
                    String[] args = new String[]{"%" + cleanName + "%"};

                    try (Cursor cursor = getContentResolver().query(
                            MediaStore.Video.Media.EXTERNAL_CONTENT_URI,
                            new String[]{MediaStore.Video.Media._ID},
                            selection,
                            args,
                            MediaStore.Video.Media._ID + " DESC"
                    )) {
                        if (cursor != null && cursor.moveToFirst()) {
                            long id = cursor.getLong(cursor.getColumnIndexOrThrow(MediaStore.Video.Media._ID));
                            mediaUri = ContentUris.withAppendedId(MediaStore.Video.Media.EXTERNAL_CONTENT_URI, id);
                        }
                    }

                    if (mediaUri != null) {
                        Intent intent = new Intent(Intent.ACTION_VIEW);
                        intent.setDataAndType(mediaUri, "video/*");
                        intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_ACTIVITY_NEW_TASK);
                        startActivity(intent);
                        return;
                    }
                } catch (Exception ignored) {}

                try {
                    Intent intent = new Intent(DownloadManager.ACTION_VIEW_DOWNLOADS);
                    intent.setFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                    startActivity(intent);
                } catch (Exception e) {
                    Toast.makeText(context, "Check " + filename + " in your Downloads app", Toast.LENGTH_SHORT).show();
                }
            });
        }
    }

    @Override
    public void onBackPressed() {
        if (webView != null && webView.canGoBack()) {
            webView.goBack();
        } else {
            super.onBackPressed();
        }
    }
}
