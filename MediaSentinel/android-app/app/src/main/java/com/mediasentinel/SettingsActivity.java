package com.mediasentinel;

import android.app.Activity;
import android.content.SharedPreferences;
import android.os.Bundle;
import android.widget.Button;
import android.widget.EditText;
import android.widget.Toast;

public class SettingsActivity extends Activity {

    private static final String PREFS_NAME   = "MediaSentinelPrefs";
    private static final String PREF_SERVER  = "server_url";
    private static final String PREF_SEER    = "seer_url";
    private static final String DEFAULT_SERVER = "http://192.168.1.100:8765";
    private static final String DEFAULT_SEER   = "http://192.168.1.100:5055";

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_settings);

        SharedPreferences prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE);

        EditText urlInput  = findViewById(R.id.urlInput);
        EditText seerInput = findViewById(R.id.seerInput);
        Button saveButton   = findViewById(R.id.saveButton);
        Button cancelButton = findViewById(R.id.cancelButton);
        Button resetButton  = findViewById(R.id.resetButton);

        urlInput.setText(prefs.getString(PREF_SERVER, DEFAULT_SERVER));
        seerInput.setText(prefs.getString(PREF_SEER, DEFAULT_SEER));
        urlInput.requestFocus();

        saveButton.setOnClickListener(v -> {
            String newUrl  = normalise(urlInput.getText().toString().trim());
            String newSeer = normalise(seerInput.getText().toString().trim());

            if (newUrl.isEmpty()) {
                Toast.makeText(this, "Please enter a MediaSentinel server URL", Toast.LENGTH_SHORT).show();
                return;
            }
            prefs.edit()
                .putString(PREF_SERVER, newUrl)
                .putString(PREF_SEER, newSeer.isEmpty() ? DEFAULT_SEER : newSeer)
                .apply();
            Toast.makeText(this, "Settings saved", Toast.LENGTH_SHORT).show();
            finish();
        });

        cancelButton.setOnClickListener(v -> finish());

        resetButton.setOnClickListener(v -> {
            urlInput.setText(DEFAULT_SERVER);
            seerInput.setText(DEFAULT_SEER);
            prefs.edit()
                .putString(PREF_SERVER, DEFAULT_SERVER)
                .putString(PREF_SEER, DEFAULT_SEER)
                .apply();
            Toast.makeText(this, "Reset to defaults", Toast.LENGTH_SHORT).show();
        });
    }

    /** Ensure URL has http:// scheme and no trailing slash. */
    private String normalise(String url) {
        if (url.isEmpty()) return url;
        if (!url.startsWith("http://") && !url.startsWith("https://")) {
            url = "http://" + url;
        }
        if (url.endsWith("/")) {
            url = url.substring(0, url.length() - 1);
        }
        return url;
    }
}
