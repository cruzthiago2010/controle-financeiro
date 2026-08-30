// Tradução das quatro páginas que ficam FORA do app: /login, /registro,
// /esqueci-senha e /redefinir-senha.
//
// Elas não carregam o index.html, então não tinham nem a tabela TRADUCOES nem
// o t() de lá — e era por isso que continuavam inteiras em português mesmo com
// o app em inglês. A auditoria de 30/08/2026 achou 37 textos assim, e duas
// dessas páginas são justamente as que nasceram nesta rodada.
//
// Tabela separada de propósito: estas páginas são carregadas por quem ainda
// NÃO entrou, e arrastar as ~600 entradas do app para cá só para traduzir umas
// poucas frases seria peso na tela mais crítica do sistema.
//
// O idioma é o mesmo `localStorage.idioma` que o app grava; quem nunca trocou
// vê português, como antes. Não há seletor de idioma aqui — a escolha vive no
// app, e oferecer um segundo lugar para trocar daria dois estados para manter
// em sincronia.

const TRADUCOES_PAGINAS = {"en": {
// --- comuns às quatro páginas ---
"Seu Controle Financeiro": "Your Money, Sorted",
"Software livre —": "Free software —",
"código-fonte": "source code",
"sob AGPL-3.0.": "under AGPL-3.0.",
"Entrar": "Sign in",
"ou": "or",
"Mostrar senha": "Show password",
"Ocultar senha": "Hide password",
"seu@email.com": "you@email.com",

// --- /login ---
"Entrar — FinanCerto": "Sign in — FinanCerto",
"Bem-vindo ao FinanCerto": "Welcome to FinanCerto",
"Acesse sua conta para começar.": "Sign in to get started.",
"Digite seu usuário": "Enter your username",
"Digite sua senha": "Enter your password",
"Esqueci minha senha": "I forgot my password",
"Entrando...": "Signing in...",
"Continuar com Google": "Continue with Google",
"Não tem uma conta?": "No account yet?",
"Crie agora.": "Create one.",
"Seus dados ficam no seu servidor.": "Your data stays on your server.",
"Não foi possível entrar": "Could not sign in",
"Login com Google não está configurado nesse servidor.": "Google sign-in is not configured on this server.",
"Não foi possível entrar com o Google. Tente de novo.": "Could not sign in with Google. Try again.",
"Tem app para Android": "There is an Android app",
"Abre mais rápido e adiciona <b>bloqueio por digital</b>, aviso de contas vencendo e widget de saldo na tela inicial.": "It opens faster and adds <b>fingerprint lock</b>, due-bill alerts and a balance widget on your home screen.",
"Baixar o app": "Download the app",
"Agora não": "Not now",
"Instale na tela de início": "Add it to your home screen",
"No iPhone não existe instalador, mas dá para deixar o FinanCerto como um app de verdade, em tela cheia:": "The iPhone has no installer, but you can still keep FinanCerto as a real full-screen app:",
"Toque em <b>Compartilhar</b> na barra do Safari": "Tap <b>Share</b> in the Safari bar",
"Escolha <b>Adicionar à Tela de Início</b>": "Choose <b>Add to Home Screen</b>",
"Confirme em <b>Adicionar</b>": "Confirm with <b>Add</b>",
"Entendi": "Got it",

// --- /registro ---
"Criar casa — FinanCerto": "Create household — FinanCerto",
"Crie sua casa no FinanCerto": "Create your household in FinanCerto",
"Seus dados ficam isolados de qualquer outra casa.": "Your data is isolated from every other household.",
"Nome da casa (ex: \"Família Silva\")": "Household name (e.g. \"The Smiths\")",
"Seu nome": "Your name",
"Usuário (pra fazer login)": "Username (to sign in)",
"E-mail (opcional, para recuperar a senha)": "Email (optional, to recover your password)",
"Senha (mín. 8 caracteres)": "Password (min. 8 characters)",
"Criar minha casa": "Create my household",
"Criando...": "Creating...",
"Criar minha casa com Google": "Create my household with Google",
"Sem e-mail cadastrado, quem esquece a senha depende do administrador da casa — e o administrador é você, então não haveria a quem recorrer.": "With no email on file, whoever forgets the password depends on the household administrator — and that is you, so there would be nobody to turn to.",
"Você vira o administrador dessa casa. Ninguém de outra casa vê seus dados, e você não vê os de ninguém.": "You become this household's administrator. Nobody from another household sees your data, and you see nobody else's.",
"Já tem conta?": "Already have an account?",
"Não foi possível criar a casa": "Could not create the household",

// --- /esqueci-senha ---
"Esqueci minha senha — FinanCerto": "Forgot my password — FinanCerto",
"Informe seu e-mail ou seu nome de usuário. Se houver uma conta, mandamos um link para você escolher uma senha nova.": "Enter your email or your username. If an account exists, we will send a link for you to choose a new password.",
"E-mail ou nome de usuário": "Email or username",
"Enviar link": "Send link",
"Enviando...": "Sending...",
"Lembrou?": "Remembered it?",
"Não foi possível enviar agora. Tente de novo em instantes.": "Could not send right now. Try again in a moment.",
"Se houver uma conta com esse endereço, o link foi enviado.": "If an account exists for that address, the link was sent.",

// --- /redefinir-senha ---
"Redefinir senha — FinanCerto": "Reset password — FinanCerto",
"Redefinir senha": "Reset password",
"Conferindo o link...": "Checking the link...",
"Link expirado": "Link expired",
"Este link já foi usado ou passou da validade. Peça um novo — leva um minuto.": "This link was already used or has expired. Ask for a new one — it takes a minute.",
"Pedir outro link": "Ask for another link",
"Lembrou a senha?": "Remembered your password?",
"Escolha uma senha nova": "Choose a new password",
"Nova senha (mín. 8 caracteres)": "New password (min. 8 characters)",
"Repita a senha": "Repeat the password",
"Salvar senha": "Save password",
"Salvando...": "Saving...",
"Conta @{0}, de {1}.": "Account @{0}, of {1}.",
"As duas senhas não são iguais.": "The two passwords do not match.",
"Não foi possível redefinir a senha.": "Could not reset the password.",
"Senha redefinida": "Password reset",
"Pronto. Entre com a senha nova — as sessões abertas nos outros aparelhos foram encerradas.": "Done. Sign in with the new password — the sessions open on your other devices were closed."
}};

const idiomaPagina = (() => {
  try { return localStorage.getItem("idioma") || "pt"; } catch (e) { return "pt"; }
})();

function t(texto){
  if (idiomaPagina === "pt") return texto;
  const tabela = TRADUCOES_PAGINAS[idiomaPagina];
  return (tabela && tabela[texto]) || texto;
}

// Mesmo tf() do app: frase com valor no meio não pode ser quebrada em pedaços,
// porque em inglês a ordem às vezes muda.
function tf(molde, ...valores){
  return valores.reduce((txt, v, i) => txt.split("{" + i + "}").join(v), t(molde));
}

// Cabeçalho das chamadas de API destas páginas. Sem o X-Idioma, o erro que o
// servidor devolve (`msg()`) volta em português no meio de uma tela inglesa —
// e é justamente aqui que a mensagem de erro mais importa.
function cabecalhoJSON(){
  return {"Content-Type": "application/json", "X-Idioma": idiomaPagina};
}

function aplicarIdiomaPagina(){
  document.documentElement.lang = idiomaPagina === "en" ? "en" : "pt-BR";
  document.title = t(document.title);
  document.querySelectorAll("[data-i18n]").forEach(el => {
    el.textContent = t(el.dataset.i18n);
  });
  // Marcação separada para o texto que tem <b> dentro: textContent apagaria a
  // marcação, e o app já usa essa mesma divisão.
  document.querySelectorAll("[data-i18n-html]").forEach(el => {
    el.innerHTML = t(el.dataset.i18nHtml);
  });
  document.querySelectorAll("[data-i18n-placeholder]").forEach(el => {
    el.placeholder = t(el.dataset.i18nPlaceholder);
  });
  document.querySelectorAll("[data-i18n-title]").forEach(el => {
    el.title = t(el.dataset.i18nTitle);
    // O botão do olho tem title E aria-label com o mesmo texto. Traduzir só um
    // deixaria o leitor de tela em português numa página em inglês, e inventar
    // um `data-i18n-aria-label` criaria uma quarta marcação para o mesmo texto.
    if (el.hasAttribute("aria-label")) el.setAttribute("aria-label", t(el.dataset.i18nTitle));
  });
}

document.addEventListener("DOMContentLoaded", aplicarIdiomaPagina);
