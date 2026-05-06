(function () {
  function digits(value, max) {
    var clean = String(value || '').replace(/\D/g, '');
    return max ? clean.slice(0, max) : clean;
  }

  function maskCep(value) {
    var d = digits(value, 8);
    if (d.length > 5) return d.slice(0, 5) + '-' + d.slice(5);
    return d;
  }

  function maskCpf(value) {
    var d = digits(value, 11);
    var out = d;
    if (d.length > 9) {
      out = d.slice(0, 3) + '.' + d.slice(3, 6) + '.' + d.slice(6, 9) + '-' + d.slice(9);
    } else if (d.length > 6) {
      out = d.slice(0, 3) + '.' + d.slice(3, 6) + '.' + d.slice(6);
    } else if (d.length > 3) {
      out = d.slice(0, 3) + '.' + d.slice(3);
    }
    return out;
  }

  function maskPhone(value) {
    var d = digits(value, 11);
    if (d.length > 10) {
      return '(' + d.slice(0, 2) + ') ' + d.slice(2, 7) + '-' + d.slice(7);
    }
    if (d.length > 6) {
      return '(' + d.slice(0, 2) + ') ' + d.slice(2, 6) + '-' + d.slice(6);
    }
    if (d.length > 2) {
      return '(' + d.slice(0, 2) + ') ' + d.slice(2);
    }
    if (d.length > 0) {
      return '(' + d;
    }
    return '';
  }

  function maskCnpj(value) {
    var d = digits(value, 14);
    if (d.length > 12) return d.slice(0,2)+'.'+d.slice(2,5)+'.'+d.slice(5,8)+'/'+d.slice(8,12)+'-'+d.slice(12);
    if (d.length > 8)  return d.slice(0,2)+'.'+d.slice(2,5)+'.'+d.slice(5,8)+'/'+d.slice(8);
    if (d.length > 5)  return d.slice(0,2)+'.'+d.slice(2,5)+'.'+d.slice(5);
    if (d.length > 2)  return d.slice(0,2)+'.'+d.slice(2);
    return d;
  }

  function applyMask(input) {
    var kind = input.dataset.mask;
    if (kind === 'cep')  input.value = maskCep(input.value);
    if (kind === 'cpf')  input.value = maskCpf(input.value);
    if (kind === 'cnpj') input.value = maskCnpj(input.value);
    if (kind === 'phone') input.value = maskPhone(input.value);
  }

  function setCepStatus(text, state) {
    var el = document.querySelector('[data-cep-status]');
    if (!el) return;
    el.textContent = text || '';
    el.dataset.state = state || '';
  }

  async function autofillCep(input) {
    var d = digits(input.value, 8);
    if (d.length !== 8) {
      setCepStatus('', '');
      return;
    }

    // Detectar se é cliente ou obra baseado no ID do input
    var isCliente = input.id === 'cep';
    var ruaId = isCliente ? 'rua' : 'obra_rua';
    var bairroId = isCliente ? 'bairro' : 'obra_bairro';

    var rua = document.getElementById(ruaId);
    var bairro = document.getElementById(bairroId);
    if (!rua || !bairro) return;

    setCepStatus('Buscando endereço...', 'loading');
    try {
      var resp = await fetch('https://viacep.com.br/ws/' + d + '/json/');
      if (!resp.ok) {
        setCepStatus('Não foi possível buscar o CEP agora.', 'error');
        return;
      }
      var dados = await resp.json();
      if (dados.erro) {
        setCepStatus('CEP não encontrado.', 'error');
        return;
      }
      if (!rua.value && dados.logradouro) rua.value = dados.logradouro;
      if (!bairro.value) {
        if (dados.bairro) {
          bairro.value = dados.bairro;
        } else if (dados.logradouro) {
          // Se não há bairro, tenta extrair de tipos comuns de logradouro
          var logradouro = dados.logradouro.toLowerCase();
          if (logradouro.includes('rua') || logradouro.includes('avenida') || logradouro.includes('alameda')) {
            bairro.value = 'Centro'; // Valor padrão para Bauru quando não há bairro específico
          } else {
            bairro.value = dados.logradouro; // Fallback para o próprio logradouro
          }
        }
      }
      setCepStatus('Endereço preenchido pelo CEP.', 'ok');
    } catch (e) {
      setCepStatus('Busca de CEP indisponível. Preencha manualmente.', 'error');
    }
  }

  document.querySelectorAll('[data-mask]').forEach(function (input) {
    applyMask(input);
    input.addEventListener('input', function () {
      applyMask(input);
    });
  });

  document.querySelectorAll('[data-cep-autofill]').forEach(function (input) {
    input.addEventListener('blur', function () {
      applyMask(input);
      autofillCep(input);
    });
  });

  document.querySelectorAll('form').forEach(function (form) {
    form.addEventListener('submit', function () {
      form.querySelectorAll('[data-mask]').forEach(applyMask);
      var button = form.querySelector('button[type="submit"]');
      if (button) button.classList.add('is-loading');
    });
  });
})();
