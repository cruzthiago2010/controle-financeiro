package com.controlefinanceiro.app

import android.annotation.SuppressLint
import android.content.Intent
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.util.Log
import android.view.View
import android.webkit.CookieManager
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import androidx.activity.addCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.biometric.BiometricManager
import androidx.biometric.BiometricPrompt
import androidx.core.content.ContextCompat
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import java.util.concurrent.TimeUnit

// O endereço do servidor não é fixo: cada pessoa hospeda o seu e informa na
// primeira abertura (veja Servidor.kt e SetupActivity.kt).

class MainActivity : AppCompatActivity() {

    private lateinit var webView: WebView
    private lateinit var appUrl: String
    private lateinit var swipeRefresh: SwipeRefreshLayout
    private lateinit var errorView: View
    private var fileUploadCallback: ValueCallback<Array<Uri>>? = null

    private fun showError() {
        swipeRefresh.isRefreshing = false
        swipeRefresh.visibility = View.GONE
        errorView.visibility = View.VISIBLE
    }

    private fun hideError() {
        errorView.visibility = View.GONE
        swipeRefresh.visibility = View.VISIBLE
    }

    private fun hasInternet(): Boolean {
        val cm = getSystemService(ConnectivityManager::class.java) ?: return false
        val network = cm.activeNetwork ?: return false
        val capabilities = cm.getNetworkCapabilities(network) ?: return false
        return capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET) &&
            capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED)
    }

    private val fileChooserLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
        val callback = fileUploadCallback
        fileUploadCallback = null
        if (callback == null) return@registerForActivityResult
        val data = result.data
        if (result.resultCode != RESULT_OK || data == null) {
            callback.onReceiveValue(null)
            return@registerForActivityResult
        }
        val uris: Array<Uri> = when {
            data.clipData != null -> {
                val clip = data.clipData!!
                Array(clip.itemCount) { i -> clip.getItemAt(i).uri }
            }
            data.data != null -> arrayOf(data.data!!)
            else -> emptyArray()
        }
        callback.onReceiveValue(uris)
    }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // Sem endereço configurado não há o que carregar: manda pro setup.
        val salvo = Servidor.url(this)
        if (salvo == null) {
            startActivity(Intent(this, SetupActivity::class.java))
            finish()
            return
        }
        appUrl = salvo

        setContentView(R.layout.activity_main)

        webView = findViewById(R.id.webView)
        swipeRefresh = findViewById(R.id.swipeRefresh)
        errorView = findViewById(R.id.errorView)
        findViewById<Button>(R.id.retryButton).setOnClickListener {
            if (hasInternet()) {
                hideError()
                webView.loadUrl(appUrl)
            } else {
                android.widget.Toast.makeText(
                    this, "Ainda sem internet", android.widget.Toast.LENGTH_SHORT
                ).show()
            }
        }

        findViewById<Button>(R.id.trocarServidorButton).setOnClickListener {
            startActivity(Intent(this, SetupActivity::class.java))
            finish()
        }

        CookieManager.getInstance().setAcceptCookie(true)
        CookieManager.getInstance().setAcceptThirdPartyCookies(webView, true)

        webView.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            databaseEnabled = true
            allowFileAccess = true
            useWideViewPort = true
            loadWithOverviewMode = true
            mediaPlaybackRequiresUserGesture = false
        }

        webView.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(
                view: WebView,
                request: WebResourceRequest
            ): Boolean {
                // Mantém tudo dentro do app; nunca sai pro navegador.
                return false
            }

            override fun onPageStarted(view: WebView, url: String, favicon: android.graphics.Bitmap?) {
                // "about:blank" é usado internamente pra limpar a página de erro do
                // próprio WebView — não deve esconder nossa tela de erro customizada.
                if (url != "about:blank") {
                    hideError()
                }
            }

            override fun onPageFinished(view: WebView, url: String) {
                swipeRefresh.isRefreshing = false
                // Garante que o cookie de sessão (login) seja gravado em disco.
                // Sem isso, se o Android matar o processo em segundo plano, a
                // sessão se perde e o app volta a pedir usuário/senha mesmo
                // depois de desbloquear com biometria.
                CookieManager.getInstance().flush()
            }

            override fun onReceivedError(
                view: WebView,
                request: WebResourceRequest,
                error: android.webkit.WebResourceError
            ) {
                Log.w("ControleFinanceiro", "Erro ao carregar: ${error.description}")
                if (request.isForMainFrame) {
                    // Sem isso, o WebView carrega a própria página de erro por baixo
                    // da nossa tela de erro, e ela reaparece se ele voltar a ficar visível.
                    view.stopLoading()
                    view.loadUrl("about:blank")
                    showError()
                }
            }
        }

        webView.webChromeClient = object : WebChromeClient() {
            override fun onShowFileChooser(
                view: WebView,
                callback: ValueCallback<Array<Uri>>,
                params: FileChooserParams
            ): Boolean {
                fileUploadCallback?.onReceiveValue(null)
                fileUploadCallback = callback
                val intent = params.createIntent()
                return try {
                    fileChooserLauncher.launch(intent)
                    true
                } catch (e: Exception) {
                    fileUploadCallback = null
                    false
                }
            }
        }

        swipeRefresh.setOnRefreshListener { webView.reload() }

        onBackPressedDispatcher.addCallback(this) {
            if (webView.canGoBack()) {
                webView.goBack()
            } else {
                isEnabled = false
                onBackPressedDispatcher.onBackPressed()
            }
        }

        pedirPermissaoNotificacao()
        agendarVerificacaoDeContas()
        agendarAtualizacaoWidget()

        if (savedInstanceState == null) {
            autenticarEAbrir()
        }
    }

    private val permissaoNotificacaoLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { /* se negar, o app funciona normalmente, só não notifica */ }

    private fun pedirPermissaoNotificacao() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            val permissao = android.Manifest.permission.POST_NOTIFICATIONS
            if (ContextCompat.checkSelfPermission(this, permissao) != android.content.pm.PackageManager.PERMISSION_GRANTED) {
                permissaoNotificacaoLauncher.launch(permissao)
            }
        }
    }

    private fun agendarVerificacaoDeContas() {
        val pedido = PeriodicWorkRequestBuilder<BillCheckWorker>(12, TimeUnit.HOURS).build()
        WorkManager.getInstance(applicationContext).enqueueUniquePeriodicWork(
            "verificar_contas_vencendo", ExistingPeriodicWorkPolicy.KEEP, pedido
        )
    }

    private fun agendarAtualizacaoWidget() {
        val pedido = PeriodicWorkRequestBuilder<SaldoWidgetWorker>(30, TimeUnit.MINUTES).build()
        WorkManager.getInstance(applicationContext).enqueueUniquePeriodicWork(
            "atualizar_widget_saldo_periodico", ExistingPeriodicWorkPolicy.KEEP, pedido
        )
    }

    /** Pede biometria/PIN do celular antes de abrir o app, se o aparelho tiver algum
     * bloqueio configurado. Se não tiver (sem PIN, sem digital), abre direto —
     * não faz sentido travar quem não protege nem a tela do celular. */
    private fun autenticarEAbrir() {
        val gerenciador = BiometricManager.from(this)
        val disponivel = gerenciador.canAuthenticate(
            BiometricManager.Authenticators.BIOMETRIC_WEAK or BiometricManager.Authenticators.DEVICE_CREDENTIAL
        )
        if (disponivel != BiometricManager.BIOMETRIC_SUCCESS) {
            webView.loadUrl(appUrl)
            return
        }

        val prompt = BiometricPrompt(this, ContextCompat.getMainExecutor(this),
            object : BiometricPrompt.AuthenticationCallback() {
                override fun onAuthenticationSucceeded(result: BiometricPrompt.AuthenticationResult) {
                    webView.loadUrl(appUrl)
                }
                override fun onAuthenticationError(errorCode: Int, errString: CharSequence) {
                    // Cancelou ou errou demais: fecha o app em vez de deixar uma tela em branco.
                    finish()
                }
            })
        val info = BiometricPrompt.PromptInfo.Builder()
            .setTitle("FinanCerto")
            .setSubtitle("Desbloqueie pra ver seus dados financeiros")
            .setAllowedAuthenticators(
                BiometricManager.Authenticators.BIOMETRIC_WEAK or BiometricManager.Authenticators.DEVICE_CREDENTIAL
            )
            .build()
        prompt.authenticate(info)
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        webView.saveState(outState)
    }

    override fun onRestoreInstanceState(savedInstanceState: Bundle) {
        super.onRestoreInstanceState(savedInstanceState)
        webView.restoreState(savedInstanceState)
    }
}
