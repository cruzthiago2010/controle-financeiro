package com.controlefinanceiro.app

import android.content.Intent
import android.os.Bundle
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.ProgressBar
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import java.net.HttpURLConnection
import java.net.URL
import kotlin.concurrent.thread

/**
 * Primeira tela: pergunta onde está o servidor.
 *
 * Antes de salvar o endereço a gente tenta falar com ele. É melhor avisar aqui
 * do que deixar a pessoa cair numa tela branca sem entender se errou o
 * endereço, se o servidor está fora do ar ou se é o Wi-Fi.
 */
class SetupActivity : AppCompatActivity() {

    private lateinit var campo: EditText
    private lateinit var botao: Button
    private lateinit var aviso: TextView
    private lateinit var carregando: ProgressBar

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_setup)

        campo = findViewById(R.id.campoServidor)
        botao = findViewById(R.id.botaoConectar)
        aviso = findViewById(R.id.avisoSetup)
        carregando = findViewById(R.id.carregandoSetup)

        // Se já existe endereço salvo, chegamos aqui para trocá-lo.
        Servidor.url(this)?.let { campo.setText(it) }

        botao.setOnClickListener { conectar() }
        campo.setOnEditorActionListener { _, _, _ -> conectar(); true }
    }

    private fun mostrarAviso(texto: String) {
        aviso.text = texto
        aviso.visibility = View.VISIBLE
    }

    private fun ocupado(sim: Boolean) {
        botao.isEnabled = !sim
        campo.isEnabled = !sim
        carregando.visibility = if (sim) View.VISIBLE else View.GONE
        botao.text = if (sim) getString(R.string.setup_conectando) else getString(R.string.setup_conectar)
    }

    private fun conectar() {
        aviso.visibility = View.GONE
        val url = Servidor.normalizar(campo.text.toString())
        if (url == null) {
            mostrarAviso(getString(R.string.setup_endereco_invalido))
            return
        }
        ocupado(true)
        thread {
            val resultado = testar(url)
            runOnUiThread {
                ocupado(false)
                if (resultado == null) {
                    Servidor.salvar(this, url)
                    startActivity(Intent(this, MainActivity::class.java))
                    finish()
                } else {
                    mostrarAviso(resultado)
                }
            }
        }
    }

    /** Devolve null quando deu certo, ou a mensagem de erro para mostrar. */
    private fun testar(url: String): String? {
        return try {
            val conexao = (URL("$url/login").openConnection() as HttpURLConnection).apply {
                connectTimeout = 8000
                readTimeout = 8000
                instanceFollowRedirects = true
                requestMethod = "GET"
            }
            val codigo = conexao.responseCode
            val corpo = try {
                conexao.inputStream.bufferedReader().use { it.readText().take(4000) }
            } catch (e: Exception) { "" }
            conexao.disconnect()

            when {
                codigo !in 200..399 -> getString(R.string.setup_erro_resposta, codigo)
                // Confere que é mesmo o FinanCerto, e não outro site qualquer
                // que por acaso responde nesse endereço.
                !corpo.contains("FinanCerto", true) &&
                    !corpo.contains("loginForm", true) ->
                    getString(R.string.setup_erro_nao_e_o_app)
                else -> null
            }
        } catch (e: Exception) {
            getString(R.string.setup_erro_conexao)
        }
    }
}
