# conv_handler_process_exam2 = ConversationHandler(
    # entry_points=[CallbackQueryHandler(start_exams, pattern='premiere')],
    
    # states={
    #     WAITING_ANSWER_EXAM: [
    #         CallbackQueryHandler(receive_answer_exam),
    #     ],
    # },
    
    # fallbacks=[CommandHandler('cancel', cancel)],
    # #per_chat=True,
    # )

    # app.add_handler(conv_handler_process_exam2)


    # conv_handler_process_exam3 = ConversationHandler(
    # entry_points=[CallbackQueryHandler(start_exams, pattern='second')],
    
    # states={
    #   # WAITING_ANSWER_EXAM: [MessageHandler(filters.TEXT & ~filters.COMMAND, verification_email)],
    #    WAITING_ANSWER_EXAM: [ CallbackQueryHandler(receive_answer_exam),],
        
    # }, fallbacks=[CommandHandler('cancel', cancel)])

    # app.add_handler(conv_handler_process_exam3)


    # conv_handler_welcome = ConversationHandler(
    # entry_points=[CallbackQueryHandler(get_level_welcome, pattern='^(enregistre)$')],
    
    # states={
    #     LEVEL_WELCOME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_why_welcome)],
    #     WHY_WELCOME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_numero_whatsapp_welcome)],
    #     NUMERO_WHATSAPP_WELCOME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_mail_welcome)],
    #     MAIL_WELCOME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name_welcome)],
    #     NOM_WELCOME: [MessageHandler(filters.TEXT & ~filters.COMMAND, last_step_welcome)],
        


    # }, fallbacks=[CommandHandler('cancel', cancel)])

    # app.add_handler(conv_handler_welcome)

    
    # conv_handler = ConversationHandler(
    #     entry_points=[CommandHandler("JeMEnregistre", start)],
    #     states={
    #         WHY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_why)],
    #         WHAT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_what)],
    #         LEVEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_level)],
    #         NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)],
    #         PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone)],
    #         COUNTRY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_country)],
    #         EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_email)],
          
    #         EXPECTATIONS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_expectations)],
            
    #         DISCOVERY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_discovery)]
            
            
    #     },
    #     fallbacks=[CommandHandler("cancel", cancel)],
    # )
    # conv_handlerstart = ConversationHandler(
    #     entry_points=[CommandHandler("start", start)],
    #     states={
    #         WHY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_why)],
    #         WHAT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_what)],
    #         LEVEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_level)],
    #         NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)],
    #         PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone)],
    #         COUNTRY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_country)],
    #         EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_email)],
    #         EXPECTATIONS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_expectations)],
    #         DISCOVERY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_discovery)],
    #         DAYS:[MessageHandler(filters.TEXT & ~filters.COMMAND, select_days_coaching)],
    #         WAITING_ANSWER_1: [CallbackQueryHandler(button_callback_waiting_1, pattern='^Poursuivre$')],
    #         WAITING_ANSWER_2: [CallbackQueryHandler(button_callback_waiting_2, pattern='^(Accepte|Refus)$')]
            
            
    #     },  
    #     fallbacks=[CommandHandler("cancel", cancel)],
    # )

    # qcm_handler = ConversationHandler(
    # entry_points=[CommandHandler("creer_qcm", start_qcm_creation)],
    # states={
    #     CATEGORIE: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_categorie)],
    #     NOMBRE_QUESTIONS: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_nb_questions)],
    #     QUESTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_question)],
    #     NB_CHOIX: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_nb_choix)],
    #     CHOIX: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_choix)],
    #     REPONSE_SUIVANTE: [MessageHandler(filters.TEXT & ~filters.COMMAND, validate_choix)],
    #     validate_bad_reason: [MessageHandler(filters.TEXT & ~filters.COMMAND, validate_bad_reason)]
    # },
    # fallbacks=[CommandHandler("cancel", cancel)])

    # conv_handler_jeu = ConversationHandler(
    #     entry_points=[CommandHandler("JeParticipeAuJeuConcours", start)],
    #     states={
    #         NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)],
    #         PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone)],
    #         COUNTRY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_country)],
    #     },
    #     fallbacks=[CommandHandler("cancel", cancel)],
    # )

    # convs_handler = ConversationHandler(
    # entry_points=[CommandHandler("message", start_message)],
    # states={
    #     ASK_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_id)],
    #     ASK_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_TEXT)],
    # },
    # fallbacks=[CommandHandler("cancel", cancel)]
    # )


    

    

   
   

    # conv_handler_exercice = ConversationHandler(
    #     entry_points=[CommandHandler('add_exercice', start_add_exercice)],
    #     states={
    #         QUESTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_question)],
    #         ANSWER: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_answer)],
    #         EXPLANATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_explanation)],
    #         CATEGORIE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_categorie)]
    #     },
    #     fallbacks=[CommandHandler('cancel', cancel)]
    # )

    # app.add_handler(conv_handler_exercice)

    # conv_handler_exam = ConversationHandler(
    #     entry_points=[CommandHandler('add_exam', start_add_exam)],
    #     states={ 
    #        NAME_EXAM: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_ars_1)],
    #        ARGS_1: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_ars_2)],
    #        ARGS_2: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_ars_3)]
    #     },
    #     fallbacks=[CommandHandler('cancel', cancel)]
    # )

    # app.add_handler(conv_handler_exam)

    # app.add_handler(CommandHandler('verify_categorie', cmd_verify_categorie))
    # conv_handler_exercice_user = ConversationHandler(
    #     entry_points=[CommandHandler('commencerMesExerciesDuSeminaire', start_exercice)],
    #     states={
    #         #WAITING_ANSWER: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_answer)],
    #         WAITING_ANSWER: [ CallbackQueryHandler(receive_answer),],
    #     },
    #     fallbacks=[CommandHandler('cancel', cancel)],
    #     allow_reentry=True,
    # )

#     app.add_handler(conv_handler_exercice_user)
#     conv_handler_exercice_users = ConversationHandler(
#         entry_points=[CommandHandler('jeRecommence', start_exercice)],
#         states={
#             #WAITING_ANSWER: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_answer)],
#             WAITING_ANSWER: [ CallbackQueryHandler(receive_answer),],
#         },
#         fallbacks=[CommandHandler('cancel', cancel)],
#         allow_reentry=True,
#     )

#     app.add_handler(conv_handler_exercice_users)
#     conv_handler_rapport = ConversationHandler(
#     entry_points=[CommandHandler('rapport', start_rapport)],
#     states={
#         CHOISIR_CATEGORIE: [MessageHandler(filters.TEXT & ~filters.COMMAND, recevoir_categorie)]
#     },
#     fallbacks=[CommandHandler('cancel', cancel)]
# )
#     app.add_handler(conv_handler_rapport)

#     conv_handler_delete_user = ConversationHandler(
#     entry_points=[CommandHandler("delete_user", start_delete_user)],
#     states={
#         ASK_USER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_user_id_to_delete)]
#     },
#     fallbacks=[CommandHandler("cancel", cancel)]
# )


    # app.add_handler(conv_handler_delete_user)
    
    

    # conv_handlerMsg = ConversationHandler(
    # entry_points=[CommandHandler('msgMasse', handle_who)],
    # states={
    #     WHO: [MessageHandler(filters.Regex('^[1-6]$'), choose_format)],
    #     CHOOSE_FORMAT: [MessageHandler(filters.Regex('^[1-5]$'), handle_format_choice)],
    #     GET_MEDIA: [MessageHandler(filters.PHOTO | filters.VIDEO, get_media)],
    #     GET_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_text)],
    # },
    # fallbacks=[CommandHandler('cancel', cancel)],
    # )

    # app.add_handler(CommandHandler("peopleCategorie", user_list_in_categories))
    # app.add_handler(CommandHandler("peopleCategorie_1", user_list_in_categorie)) 
    # app.add_handler(CommandHandler("peopleCategorie_2", user_list_in_categorie_1)) 
    # app.add_handler(CommandHandler("mes_seances", user_list_in_categorie_2)) 
    # app.add_handler(CommandHandler("now", try_mail))
    
    # app.add_handler(conv_handlerMsg)

    #app.add_handler(CommandHandler("userDelete", start_delete))
  

    # conv_handler_add = ConversationHandler(
    #     entry_points=[CommandHandler('add_categorie', start_add_categorie)],
    #     states={
    #         NOM_CATEGORIE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_nom_categorie)]
    #     },
    #     fallbacks=[CommandHandler('cancel', cancel)]
    # )


    # app.add_handler(conv_handler_add)

    # conv_handler_mail = ConversationHandler(
    # entry_points=[CommandHandler('send_mail_user', send_mail_admin)],
    # states={
    #     GET_MAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, send_short_link)],
    # },
    # fallbacks=[CommandHandler('cancel', cancel)],
    # )

    # app.add_handler(conv_handler_mail)
  
    



    # app.add_handler(conv_handler)
    # app.add_handler(conv_handlerstart)
    

    # app.add_handler(CommandHandler("userInfo", user_info))
    # app.add_handler(CommandHandler("user_check_doublou", check_user_doublon))
    # app.add_handler(CommandHandler("user_delete_doublou", delete_user_doublon))
    # app.add_handler(CommandHandler("fichier_exam", send_file_user_exam))
    # app.add_handler(CommandHandler("qr_code", qr_code_generate))
    # #app.add_handler(CommandHandler("mail_none_participant", send_none_email))